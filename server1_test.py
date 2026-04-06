import queue
import json
import socket
import ssl
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

HOST = "127.0.0.1"
PORT = 5000
CERT_FILE = "server.crt"
KEY_FILE = "server.key"
QUESTIONS_FILE = "questions.json"


def load_questions(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {file_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or not data:
        raise ValueError("Questions file must contain a non-empty JSON array.")

    normalized = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {index} must be a JSON object.")

        question = str(item.get("question", "")).strip()
        options = item.get("options")
        answer = str(item.get("answer", "")).strip().upper()

        if not question:
            raise ValueError(f"Question {index} has empty 'question'.")

        if not isinstance(options, list) or len(options) < 2:
            raise ValueError(f"Question {index} must have at least 2 options.")

        clean_options = [str(opt).strip() for opt in options]
        if any(not opt for opt in clean_options):
            raise ValueError(f"Question {index} has an empty option.")

        valid_answers = {
            opt.split(")", 1)[0].strip().upper()
            for opt in clean_options
            if ")" in opt
        }
        if answer not in valid_answers:
            raise ValueError(
                f"Question {index} answer '{answer}' is invalid. "
                f"Expected one of: {', '.join(sorted(valid_answers))}"
            )

        normalized.append({"question": question, "options": clean_options, "answer": answer})

    return normalized


class QuizServerApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Quiz Server Conductor (TLS)")
        self.master.geometry("800x500")
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.questions = load_questions(QUESTIONS_FILE)

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("TButton", font=("Arial", 10, "bold"))
        self.style.configure("Danger.TButton", foreground="red")

        self.paned_window = ttk.PanedWindow(self.master, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True)

        self.left_frame = ttk.Frame(self.paned_window, padding=10)
        self.right_frame = ttk.Frame(self.paned_window, padding=10)

        self.paned_window.add(self.left_frame, weight=3)
        self.paned_window.add(self.right_frame, weight=1)

        self.status_label = ttk.Label(
            self.left_frame, text="Server Stopped", font=("Arial", 14, "bold"), foreground="red"
        )
        self.status_label.pack(pady=(0, 10))

        self.controls_frame = ttk.Frame(self.left_frame)
        self.controls_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(self.controls_frame, text="Start Server", command=self.start_server_thread)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        ttk.Label(self.controls_frame, text="Questions:").pack(side=tk.LEFT, padx=(20, 5))

        self.q_var = tk.StringVar()
        self.q_dropdown = ttk.Combobox(self.controls_frame, textvariable=self.q_var, state="readonly", width=30)
        self.q_dropdown["values"] = [f"Q{i + 1}: {q['question']}" for i, q in enumerate(self.questions)]
        if self.questions:
            self.q_dropdown.current(0)
        self.q_dropdown.pack(side=tk.LEFT, padx=5)

        self.send_q_btn = ttk.Button(
            self.controls_frame,
            text="Send Selected Question",
            command=self.send_selected_question,
            state=tk.DISABLED,
        )
        self.send_q_btn.pack(side=tk.LEFT, padx=5)

        self.end_quiz_btn = ttk.Button(
            self.controls_frame,
            text="End Quiz / Show Winners",
            command=self.end_quiz,
            style="Danger.TButton",
            state=tk.DISABLED,
        )
        self.end_quiz_btn.pack(side=tk.LEFT, padx=5)

        self.log_area = scrolledtext.ScrolledText(
            self.left_frame, wrap=tk.WORD, font=("Consolas", 10), state=tk.DISABLED
        )
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=10)

        ttk.Label(self.right_frame, text="Connected Players", font=("Arial", 12, "bold")).pack(pady=(0, 5))

        self.player_listbox = tk.Listbox(self.right_frame, font=("Consolas", 11), selectmode=tk.SINGLE)
        self.player_listbox.pack(fill=tk.BOTH, expand=True)

        self.kick_btn = ttk.Button(
            self.right_frame,
            text="Kick Selected Player",
            style="Danger.TButton",
            command=self.kick_player,
            state=tk.DISABLED,
        )
        self.kick_btn.pack(fill=tk.X, pady=(10, 0))

        self.server_socket = None
        self.clients = []
        self.usernames = {}
        self.scores = {}
        self.lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self.question_active = False
        self.ui_queue = queue.Queue()
        self.player_sock_map = {}

        self.process_queue()

    def log(self, message):
        self.ui_queue.put({"type": "log", "msg": message})

    def update_status(self, text, color="black"):
        self.ui_queue.put({"type": "status", "msg": text, "color": color})

    def refresh_player_list(self):
        with self.lock:
            user_info = [
                (self.usernames[c], self.scores.get(self.usernames[c], 0), c)
                for c in self.clients
                if c in self.usernames
            ]

        self.ui_queue.put({"type": "players", "data": user_info})

    def set_send_btn_state(self, state):
        self.ui_queue.put({"type": "btn_state", "state": state})

    def process_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                msg_type = msg["type"]

                if msg_type == "log":
                    self.log_area.config(state=tk.NORMAL)
                    self.log_area.insert(tk.END, msg["msg"] + "\n")
                    self.log_area.see(tk.END)
                    self.log_area.config(state=tk.DISABLED)
                elif msg_type == "status":
                    self.status_label.config(text=msg["msg"], foreground=msg.get("color", "black"))
                elif msg_type == "players":
                    self.player_listbox.delete(0, tk.END)
                    self.player_sock_map = {}
                    for idx, (uname, score, sock) in enumerate(msg["data"]):
                        self.player_listbox.insert(tk.END, f"{uname} (Score: {score})")
                        self.player_sock_map[idx] = sock
                    self.kick_btn.config(state=tk.NORMAL if self.player_sock_map else tk.DISABLED)
                elif msg_type == "btn_state":
                    self.send_q_btn.config(state=msg["state"])
                    self.end_quiz_btn.config(state=msg["state"])

        except queue.Empty:
            pass
        self.master.after(100, self.process_queue)

    def start_server_thread(self):
        if self.server_socket:
            return

        self.start_btn.config(state=tk.DISABLED)
        self.set_send_btn_state(tk.NORMAL)
        self.shutdown_event.clear()

        threading.Thread(target=self.run_server, daemon=True).start()

    def send_selected_question(self):
        if self.question_active:
            messagebox.showwarning("Busy", "A question is already running.")
            return

        idx = self.q_dropdown.current()
        if idx < 0:
            return

        self.question_active = True
        self.set_send_btn_state(tk.DISABLED)
        threading.Thread(target=self.fire_question_thread, args=(idx,), daemon=True).start()

    def end_quiz(self):
        if self.question_active:
            messagebox.showwarning("Busy", "Wait for the current question to finish.")
            return

        with self.lock:
            leaderboard = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

        if not leaderboard:
            board = "LEADERBOARD\nNo players joined."
        else:
            board = "LEADERBOARD\n=== FINAL RANKINGS ===\n"
            for i, (user, score) in enumerate(leaderboard):
                board += f"#{i + 1} - {user} : {score} pts\n"

            highest_score = leaderboard[0][1]
            winners = [u for u, score in leaderboard if score == highest_score]
            board += "\n" + "-" * 20 + "\n"
            if len(winners) > 1:
                board += "WINNERS (TIE): " + ", ".join(winners)
            else:
                board += f"WINNER: {winners[0]}"

        self.broadcast(board)
        self.broadcast("QUIZ_END")

        self.log("Quiz ended manually. Final leaderboard broadcasted.")
        self.update_status("Quiz Ended", "red")
        self.set_send_btn_state(tk.DISABLED)

    def kick_player(self):
        selected = self.player_listbox.curselection()
        if not selected:
            messagebox.showinfo("Select", "Please select a player to kick.")
            return

        idx = selected[0]
        sock = self.player_sock_map.get(idx)

        if sock:
            with self.lock:
                username = self.usernames.get(sock, "Unknown")
            self.log(f"Kicking player: {username}")
            self.send_line(sock, "KICK")
            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except OSError:
                pass

    def run_server(self):
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(1.0)

        try:
            self.server_socket.bind((HOST, PORT))
            self.server_socket.listen()
            self.log(f"TLS server started on {HOST}:{PORT}")
            self.update_status(f"Listening on {PORT} (TLS)", "green")

            while not self.shutdown_event.is_set():
                try:
                    raw_client, _addr = self.server_socket.accept()
                except socket.timeout:
                    continue

                try:
                    client = tls_context.wrap_socket(raw_client, server_side=True)
                except ssl.SSLError:
                    try:
                        raw_client.close()
                    except OSError:
                        pass
                    continue

                with self.lock:
                    self.clients.append(client)

                threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()
        except Exception as exc:
            self.log(f"Server error: {exc}")
        finally:
            self.cleanup()

    @staticmethod
    def send_line(sock, message):
        sock.sendall((message + "\n").encode())

    def broadcast(self, message):
        dead_clients = []

        with self.lock:
            for client in list(self.clients):
                try:
                    self.send_line(client, message)
                except OSError:
                    dead_clients.append(client)

        for dead in dead_clients:
            self.remove_client(dead)

    def remove_client(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
            if client in self.usernames:
                username = self.usernames[client]
                self.log(f"{username} disconnected.")
                del self.usernames[client]

        try:
            client.close()
        except OSError:
            pass

        self.refresh_player_list()

    def recv_next_line(self, client, buffer):
        while "\n" not in buffer and not self.shutdown_event.is_set():
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                return None, buffer
            except OSError:
                return "", buffer

            if not chunk:
                return "", buffer
            buffer += chunk.decode()

        if "\n" not in buffer:
            return None, buffer

        line, buffer = buffer.split("\n", 1)
        return line.strip(), buffer

    def handle_client(self, client):
        client.settimeout(1.0)
        recv_buffer = ""

        username = None
        while not self.shutdown_event.is_set() and not username:
            data, recv_buffer = self.recv_next_line(client, recv_buffer)
            if data == "":
                break
            if data is None:
                continue
            username = data.strip()

        if not username:
            self.remove_client(client)
            return

        with self.lock:
            existing_names = {name.casefold() for name in self.usernames.values()}
            if username.casefold() in existing_names:
                self.log(f"Rejected duplicate username: {username}")
                self.send_line(client, "USERNAME_TAKEN|Username already in use.")
                self.remove_client(client)
                return

            self.usernames[client] = username
            if username not in self.scores:
                self.scores[username] = 0

        self.log(f"{username} joined.")
        self.send_line(client, "STARTING|Welcome. Waiting for server to send questions...")
        self.refresh_player_list()

        try:
            while not self.shutdown_event.is_set():
                data, recv_buffer = self.recv_next_line(client, recv_buffer)
                if data == "":
                    break
                if data is None:
                    continue

                if "|" not in data:
                    continue

                qid_str, answer = data.split("|", 1)
                try:
                    qid = int(qid_str)
                except ValueError:
                    continue

                if qid < 0 or qid >= len(self.questions):
                    continue

                correct = self.questions[qid]["answer"]

                with self.lock:
                    if answer.upper() == correct:
                        self.scores[username] += 10
                        self.log(f"{username} answered Q{qid + 1} correctly.")
                    else:
                        self.log(f"{username} answered Q{qid + 1} incorrectly.")

                self.refresh_player_list()
        except OSError:
            pass
        finally:
            self.remove_client(client)

    def fire_question_thread(self, qid):
        question = self.questions[qid]

        self.log(f"Sending Question {qid + 1}...")
        self.update_status(f"Question {qid + 1} is running...", "blue")
        msg = f"QUESTION|{qid}|{question['question']}|" + "|".join(question["options"])
        self.broadcast(msg)

        end_time = time.time() + 10
        while time.time() < end_time:
            if self.shutdown_event.is_set():
                return
            time.sleep(0.5)

        self.broadcast("TIMEUP")
        self.log(f"Time is up for Question {qid + 1}.")

        with self.lock:
            leaderboard = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

        board = "LEADERBOARD\n=== INTERMEDIATE RANKINGS ===\n"
        for i, (user, score) in enumerate(leaderboard):
            board += f"#{i + 1} - {user} : {score} pts\n"

        self.broadcast(board)
        self.log("Leaderboard broadcasted.")
        self.update_status("Waiting to send next question", "green")

        self.question_active = False
        self.set_send_btn_state(tk.NORMAL)

    def cleanup(self):
        self.shutdown_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        for client in list(self.clients):
            self.remove_client(client)

        self.update_status("Server Stopped", "red")
        self.log("Server shut down.")

        try:
            self.start_btn.config(state=tk.NORMAL)
            self.set_send_btn_state(tk.DISABLED)
            self.kick_btn.config(state=tk.DISABLED)
            self.question_active = False
        except tk.TclError:
            pass

    def on_closing(self):
        self.cleanup()
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        app = QuizServerApp(root)
    except Exception as exc:
        messagebox.showerror("Question Load Error", str(exc))
        root.destroy()
        raise SystemExit(1)
    root.mainloop()
