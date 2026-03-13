import socket
import ssl
import threading
import time

HOST = "127.0.0.1"
PORT = 5000
CERT_FILE = "server.crt"
KEY_FILE = "server.key"

clients = []
usernames = {}
scores = {}
lock = threading.Lock()
MIN_PLAYERS = 3
quiz_started = False
shutdown_event = threading.Event()

questions = [
    {
        "question": "Capital of India?",
        "options": ["A) Delhi", "B) Mumbai", "C) Chennai", "D) Kolkata"],
        "answer": "A"
    },
    {
        "question": "2 + 2 = ?",
        "options": ["A) 3", "B) 4", "C) 5", "D) 6"],
        "answer": "B"
    }
]


def send_line(sock, message):
    sock.sendall((message + "\n").encode())

def broadcast(message):
    dead_clients = []
    for client in list(clients):
        try:
            send_line(client, message)
        except:
            dead_clients.append(client)

    if dead_clients:
        with lock:
            for dead in dead_clients:
                if dead in clients:
                    clients.remove(dead)
                if dead in usernames:
                    del usernames[dead]
                try:
                    dead.close()
                except:
                    pass


def recv_next_line(client, buffer):
    while "\n" not in buffer and not shutdown_event.is_set():
        try:
            chunk = client.recv(4096)
        except socket.timeout:
            return None, buffer
        if not chunk:
            return "", buffer
        buffer += chunk.decode()

    if "\n" not in buffer:
        return None, buffer

    line, buffer = buffer.split("\n", 1)
    return line.strip(), buffer


def handle_client(client):
    global quiz_started

    client.settimeout(1.0)
    recv_buffer = ""

    username = None
    while not shutdown_event.is_set() and not username:
        try:
            data, recv_buffer = recv_next_line(client, recv_buffer)
            if data == "":
                break
            if data is None:
                continue
            username = data.strip()
        except:
            break

    if not username:
        client.close()
        return

    with lock:
        usernames[client] = username
        scores[username] = 0

    print(f"{username} joined")

    with lock:
        if (not quiz_started) and len(clients) >= MIN_PLAYERS:
            quiz_started = True
            threading.Thread(target=quiz_loop, daemon=True).start()
            broadcast(f"STARTING|{MIN_PLAYERS} players joined. Quiz is starting...")

    try:
        while not shutdown_event.is_set():
            try:
                data, recv_buffer = recv_next_line(client, recv_buffer)
            except:
                break
            if data == "":
                break
            if data is None:
                continue

            if "|" not in data:
                continue

            qid_str, ans = data.split("|", 1)
            try:
                qid = int(qid_str)
            except ValueError:
                continue

            if qid < 0 or qid >= len(questions):
                continue

            correct = questions[qid]["answer"]

            with lock:
                if ans.upper() == correct:
                    scores[username] += 10

    except:
        pass

    finally:
        with lock:
            if client in clients:
                clients.remove(client)
            if client in usernames:
                del usernames[client]

        client.close()


def quiz_loop():
    for qid, q in enumerate(questions):
        if shutdown_event.is_set():
            return

        msg = f"QUESTION|{qid}|{q['question']}|" + "|".join(q["options"])
        broadcast(msg)

        print("Question sent")

        # Keep answer window responsive to shutdown.
        end_time = time.time() + 10
        while time.time() < end_time:
            if shutdown_event.is_set():
                return
            time.sleep(0.2)

        broadcast("TIMEUP")

        with lock:
            leaderboard = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        board = "LEADERBOARD\n"
        for u, s in leaderboard:
            board += f"{u} : {s}\n"

        broadcast(board)

    broadcast("QUIZ_END")


def main():

    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(1.0)
    server.bind((HOST, PORT))
    server.listen()

    print("TLS server started")

    try:
        while not shutdown_event.is_set():
            try:
                raw_client, addr = server.accept()
            except socket.timeout:
                continue

            try:
                client = tls_context.wrap_socket(raw_client, server_side=True)
            except ssl.SSLError:
                try:
                    raw_client.close()
                except:
                    pass
                continue

            with lock:
                clients.append(client)

            threading.Thread(target=handle_client, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        shutdown_event.set()
        try:
            server.close()
        except:
            pass

        with lock:
            for client in list(clients):
                try:
                    client.close()
                except:
                    pass
            clients.clear()


if __name__ == "__main__":
    main()