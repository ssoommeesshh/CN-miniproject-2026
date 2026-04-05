import queue
import socket
import ssl
import threading
import tkinter as tk
from tkinter import messagebox

# Modern UI palette
BG_COLOR = "#1e1e2e"
FG_COLOR = "#cdd6f4"
ACCENT_COLOR = "#89b4fa"
HOVER_COLOR = "#b4befe"
DANGER_COLOR = "#f38ba8"
SUCCESS_COLOR = "#a6e3a1"
FRAME_BG = "#313244"

FONT_TITLE = ("Segoe UI", 28, "bold")
FONT_SUBTITLE = ("Segoe UI", 18, "bold")
FONT_NORMAL = ("Segoe UI", 14)
FONT_BTN = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 14)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
CA_CERT_FILE = "server.crt"


class ModernButton(tk.Button):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("bg", ACCENT_COLOR)
        kwargs.setdefault("fg", "#11111b")
        kwargs.setdefault("activebackground", HOVER_COLOR)
        kwargs.setdefault("activeforeground", "#11111b")
        kwargs.setdefault("font", FONT_BTN)
        kwargs.setdefault("relief", tk.FLAT)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("padx", 15)
        kwargs.setdefault("pady", 8)

        super().__init__(master, **kwargs)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.default_bg = kwargs["bg"]

    def on_enter(self, _event):
        if self["state"] != tk.DISABLED:
            self["background"] = self["activebackground"]

    def on_leave(self, _event):
        if self["state"] != tk.DISABLED:
            self["background"] = self.default_bg


class QuizClientApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Quiz Client (TLS)")
        self.master.geometry("700x500")
        self.master.configure(bg=BG_COLOR)
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.sock = None
        self.username = ""
        self.current_qid = None
        self.stop_event = threading.Event()
        self.ui_queue = queue.Queue()

        self.frames = {}
        for frame_class in (LoginFrame, LobbyFrame, QuestionFrame, LeaderboardFrame):
            page_name = frame_class.__name__
            frame = frame_class(parent=self.master, controller=self)
            self.frames[page_name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.master.grid_rowconfigure(0, weight=1)
        self.master.grid_columnconfigure(0, weight=1)

        self.show_frame("LoginFrame")
        self.process_queue()

    def show_frame(self, page_name):
        frame = self.frames[page_name]
        frame.tkraise()
        if hasattr(frame, "on_show"):
            frame.on_show()

    def process_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                msg_type = msg["type"]

                if msg_type == "STARTING":
                    self.frames["LobbyFrame"].update_message(msg["data"])
                elif msg_type == "QUESTION":
                    qid, question, options = msg["data"]
                    self.current_qid = qid
                    self.frames["QuestionFrame"].display_question(question, options)
                    self.show_frame("QuestionFrame")
                elif msg_type == "TIMEUP":
                    self.current_qid = None
                    self.frames["QuestionFrame"].stop_timer()
                    self.frames["QuestionFrame"].disable_buttons()
                    self.frames["QuestionFrame"].set_status(
                        "Time's up! Waiting for leaderboard...", text_color=SUCCESS_COLOR
                    )
                elif msg_type == "LEADERBOARD":
                    self.frames["LeaderboardFrame"].update_leaderboard(msg["data"])
                    self.show_frame("LeaderboardFrame")
                elif msg_type == "QUIZ_END":
                    self.frames["LeaderboardFrame"].set_status("Quiz finished!", text_color=SUCCESS_COLOR)
                    self.stop_event.set()
                elif msg_type == "KICK":
                    messagebox.showerror("Kicked", "You have been kicked by the server.")
                    self.stop_connection()
                    self.show_frame("LoginFrame")
                elif msg_type == "DISCONNECT":
                    messagebox.showerror("Disconnected", "Lost connection to the server.")
                    self.stop_connection()
                    self.show_frame("LoginFrame")
        except queue.Empty:
            pass

        self.master.after(50, self.process_queue)

    def connect(self, host, port, username):
        self.username = username
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(3.0)

        tls_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        tls_context.check_hostname = False
        tls_context.load_verify_locations(cafile=CA_CERT_FILE)

        try:
            self.sock = tls_context.wrap_socket(raw_sock, server_hostname=host)
            self.sock.connect((host, port))
            self.sock.sendall((username + "\n").encode())
        except Exception as exc:
            try:
                raw_sock.close()
            except OSError:
                pass
            messagebox.showerror("Connection Error", f"Could not connect to {host}:{port}\n{exc}")
            return False

        self.stop_event.clear()
        threading.Thread(target=self.receive_messages, daemon=True).start()
        self.show_frame("LobbyFrame")
        return True

    def send_answer(self, option_char):
        if self.current_qid is None:
            return

        if self.sock:
            try:
                answer_payload = f"{self.current_qid}|{option_char}\n"
                self.sock.sendall(answer_payload.encode())
                self.frames["QuestionFrame"].stop_timer()
                self.frames["QuestionFrame"].disable_buttons()
                self.frames["QuestionFrame"].set_status(
                    "Answer sent. Waiting for others...", text_color=SUCCESS_COLOR
                )
            except OSError:
                self.ui_queue.put({"type": "DISCONNECT"})

    @staticmethod
    def extract_lines(buffer):
        lines = []
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if line:
                lines.append(line)
        return lines, buffer

    def receive_messages(self):
        self.sock.settimeout(1.0)
        recv_buffer = ""

        while not self.stop_event.is_set():
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    self.ui_queue.put({"type": "DISCONNECT"})
                break

            if not chunk:
                if not self.stop_event.is_set():
                    self.ui_queue.put({"type": "DISCONNECT"})
                break

            recv_buffer += chunk.decode()
            lines, recv_buffer = self.extract_lines(recv_buffer)

            for msg in lines:
                if msg == "KICK":
                    self.ui_queue.put({"type": "KICK"})
                elif msg.startswith("STARTING|"):
                    self.ui_queue.put({"type": "STARTING", "data": msg})
                elif msg.startswith("QUESTION|"):
                    parts = msg.split("|")
                    if len(parts) >= 4:
                        qid = parts[1]
                        question = parts[2]
                        options = parts[3:]
                        self.ui_queue.put({"type": "QUESTION", "data": (qid, question, options)})
                elif msg == "TIMEUP":
                    self.ui_queue.put({"type": "TIMEUP"})
                elif msg.startswith("LEADERBOARD"):
                    self.ui_queue.put({"type": "LEADERBOARD", "data": "LEADERBOARD"})
                elif " : " in msg:
                    self.ui_queue.put({"type": "LEADERBOARD", "data": msg})
                elif msg == "QUIZ_END":
                    self.ui_queue.put({"type": "QUIZ_END"})

    def stop_connection(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.current_qid = None

    def on_closing(self):
        self.stop_connection()
        self.master.destroy()


class LoginFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_COLOR)
        self.controller = controller

        container = tk.Frame(self, bg=FRAME_BG, padx=40, pady=40)
        container.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(container, text="Join Quiz", font=FONT_TITLE, bg=FRAME_BG, fg=FG_COLOR).pack(pady=(0, 30))

        form_frame = tk.Frame(container, bg=FRAME_BG)
        form_frame.pack()

        def create_input(row, text, default=""):
            tk.Label(form_frame, text=text, font=FONT_NORMAL, bg=FRAME_BG, fg=FG_COLOR).grid(
                row=row, column=0, padx=10, pady=10, sticky="e"
            )
            entry = tk.Entry(
                form_frame,
                font=FONT_NORMAL,
                bg=BG_COLOR,
                fg=FG_COLOR,
                insertbackground=FG_COLOR,
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=ACCENT_COLOR,
            )
            entry.insert(0, default)
            entry.grid(row=row, column=1, padx=10, pady=10)
            return entry

        self.host_entry = create_input(0, "Host IP:", DEFAULT_HOST)
        self.port_entry = create_input(1, "Port:", str(DEFAULT_PORT))
        self.user_entry = create_input(2, "Username:")

        ModernButton(container, text="Connect to Server", width=20, command=self.on_connect).pack(
            pady=(30, 0)
        )

    def on_connect(self):
        host = self.host_entry.get().strip()
        port_str = self.port_entry.get().strip()
        user = self.user_entry.get().strip()

        if not user:
            messagebox.showwarning("Input Error", "Username cannot be empty")
            return

        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("Input Error", "Port must be a number")
            return

        self.controller.connect(host, port, user)


class LobbyFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_COLOR)
        self.controller = controller

        self.lbl = tk.Label(
            self,
            text="Waiting for the quiz to begin...",
            font=FONT_SUBTITLE,
            bg=BG_COLOR,
            fg=FG_COLOR,
            wraplength=600,
            justify=tk.CENTER,
        )
        self.lbl.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    def update_message(self, msg):
        self.lbl.config(text=msg.replace("STARTING|", ""))


class QuestionFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_COLOR)
        self.controller = controller

        header = tk.Frame(self, bg=BG_COLOR)
        header.pack(fill=tk.X, padx=20, pady=10)

        self.timer_label = tk.Label(
            header, text="Time: 10s", font=FONT_SUBTITLE, bg=BG_COLOR, fg=DANGER_COLOR
        )
        self.timer_label.pack(side=tk.RIGHT)

        self.q_label = tk.Label(
            self,
            text="Question",
            font=FONT_TITLE,
            bg=BG_COLOR,
            fg=FG_COLOR,
            wraplength=600,
            justify=tk.CENTER,
        )
        self.q_label.pack(pady=(20, 40), expand=True)

        self.buttons_frame = tk.Frame(self, bg=BG_COLOR)
        self.buttons_frame.pack(expand=True, fill=tk.BOTH, padx=50)

        self.buttons = []
        for _ in range(4):
            btn = ModernButton(
                self.buttons_frame,
                text="",
                bg=FRAME_BG,
                fg=FG_COLOR,
                activebackground=HOVER_COLOR,
                width=40,
                font=FONT_NORMAL,
            )
            btn.default_bg = FRAME_BG
            btn.pack(pady=10)
            self.buttons.append(btn)

        self.status_label = tk.Label(self, text="", font=FONT_NORMAL, bg=BG_COLOR, fg=SUCCESS_COLOR)
        self.status_label.pack(pady=20)

        self.timer_id = None
        self.time_left = 10

    def display_question(self, question, options):
        self.q_label.config(text=question)
        self.status_label.config(text="")

        for index, option in enumerate(options):
            if index < len(self.buttons):
                self.buttons[index].config(text=option, state=tk.NORMAL)
                option_char = option.split(")")[0].strip()
                self.buttons[index].config(command=lambda ch=option_char: self.controller.send_answer(ch))

        self.start_timer(10)

    def start_timer(self, seconds=10):
        self.stop_timer()
        self.time_left = seconds
        self.timer_label.config(
            text=f"Time: {self.time_left}s", fg=DANGER_COLOR if seconds <= 3 else ACCENT_COLOR
        )
        self.tick()

    def tick(self):
        if self.time_left > 0:
            self.time_left -= 1
            self.timer_label.config(
                text=f"Time: {self.time_left}s",
                fg=DANGER_COLOR if self.time_left <= 3 else ACCENT_COLOR,
            )
            self.timer_id = self.after(1000, self.tick)
        else:
            self.timer_label.config(text="Time: 0s", fg=DANGER_COLOR)
            self.disable_buttons()

    def stop_timer(self):
        if self.timer_id:
            self.after_cancel(self.timer_id)
            self.timer_id = None

    def disable_buttons(self):
        for button in self.buttons:
            button.config(state=tk.DISABLED)

    def set_status(self, text, text_color=SUCCESS_COLOR):
        self.status_label.config(text=text, fg=text_color)


class LeaderboardFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg=BG_COLOR)
        self.controller = controller

        tk.Label(self, text="Leaderboard", font=FONT_TITLE, bg=BG_COLOR, fg=ACCENT_COLOR).pack(pady=20)

        self.lboard_text = tk.Text(
            self,
            width=50,
            height=12,
            state=tk.DISABLED,
            font=FONT_MONO,
            bg=FRAME_BG,
            fg=FG_COLOR,
            relief=tk.FLAT,
            padx=20,
            pady=20,
        )
        self.lboard_text.pack(pady=10)

        self.status_label = tk.Label(
            self,
            text="Waiting for next question...",
            font=FONT_NORMAL,
            bg=BG_COLOR,
            fg=FG_COLOR,
        )
        self.status_label.pack(pady=20)

    def update_leaderboard(self, row_str):
        self.lboard_text.config(state=tk.NORMAL)
        if row_str == "LEADERBOARD":
            self.lboard_text.delete(1.0, tk.END)
            self.status_label.config(text="Waiting for next question...", fg=FG_COLOR)
        else:
            self.lboard_text.insert(tk.END, row_str + "\n")

        self.lboard_text.config(state=tk.DISABLED)

    def set_status(self, text, text_color=SUCCESS_COLOR):
        self.status_label.config(text=text, fg=text_color)


if __name__ == "__main__":
    root = tk.Tk()
    app = QuizClientApp(root)
    root.mainloop()
