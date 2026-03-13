import socket
import ssl
import threading
import time

HOST = "127.0.0.1"
PORT = 5000
CA_CERT_FILE = "server.crt"
stop_event = threading.Event()
question_event = threading.Event()
state_lock = threading.Lock()
current_qid = None


def send_line(sock, message):
    sock.sendall((message + "\n").encode())


def extract_lines(buffer):
    lines = []
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        line = line.strip()
        if line:
            lines.append(line)
    return lines, buffer

def receive_messages(sock):
    global current_qid

    sock.settimeout(1.0)
    recv_buffer = ""

    while not stop_event.is_set():
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break

        if not chunk:
            break

        recv_buffer += chunk.decode()
        lines, recv_buffer = extract_lines(recv_buffer)

        for msg in lines:
            if msg.startswith("STARTING|"):
                print(msg)

            elif msg.startswith("QUESTION|"):
                parts = msg.split("|")
                if len(parts) >= 4:
                    qid = parts[1]
                    question = parts[2]
                    options = parts[3:]

                    print("\n", question)
                    for o in options:
                        print(o)

                    with state_lock:
                        current_qid = qid
                    question_event.set()

            elif msg == "TIMEUP":
                with state_lock:
                    current_qid = None
                question_event.clear()
                print("Time up!")

            elif msg.startswith("LEADERBOARD"):
                print(msg)

            elif msg == "QUIZ_END":
                print("Quiz finished")
                stop_event.set()
                break

            else:
                print(msg)

    stop_event.set()
    question_event.set()


def main():
    global current_qid

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    tls_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_context.check_hostname = False
    tls_context.load_verify_locations(cafile=CA_CERT_FILE)

    sock = tls_context.wrap_socket(raw_sock, server_hostname=HOST)
    try:
        sock.connect((HOST, PORT))

        username = input("Enter username: ")
        send_line(sock, username)

        recv_thread = threading.Thread(target=receive_messages, args=(sock,), daemon=True)
        recv_thread.start()

        while recv_thread.is_alive() and not stop_event.is_set():
            if not question_event.wait(0.2):
                continue
            if stop_event.is_set():
                break

            with state_lock:
                qid_to_answer = current_qid

            if qid_to_answer is None:
                question_event.clear()
                continue

            try:
                ans = input("Your answer: ")
            except (EOFError, KeyboardInterrupt):
                stop_event.set()
                break

            try:
                send = f"{qid_to_answer}|{ans}"
                send_line(sock, send)
            except OSError:
                stop_event.set()
                break

            with state_lock:
                current_qid = None
            question_event.clear()

        stop_event.set()
    except KeyboardInterrupt:
        print("\nStopping client...")
        stop_event.set()
    finally:
        try:
            sock.close()
        except:
            pass


if __name__ == "__main__":
    main()