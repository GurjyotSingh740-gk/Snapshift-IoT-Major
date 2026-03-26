# SnapShift â€” SYSTEM 1 (Sender)
# BTN1 = SELECT active window â†’ start move
# BTN2 = If at right edge â†’ TRANSFER to System 2 | else drop in place
# BTN3 = RESET everything New

import socket
import threading
import time
import sys
import os
import ctypes

import win32gui
import pygame

UDP_PORT    = 5005
MY_IP       = "0.0.0.0"

SYSTEM2_IP   = "192.168.0.101"    # â† Change to System 2 IP
SYSTEM2_TCP  = 6000               # TCP file receive port on System 2
SYSTEM2_UDP  = 6001               # UDP notify port on System 2

TRANSFER_DIR = r"C:\SnapShift\outbox"
SCREEN_W     = 1920
SCREEN_H     = 1080
MOVE_SENS    = 3.5
EDGE_THRESH  = SCREEN_W - 160     # file is "at edge" if x >= this

grabbed_hwnd   = None
grabbed_title  = ""
grabbed_file   = None
is_grabbed     = False

gyro_z = 0.0
gyro_y = 0.0
drag_x = 0.0
drag_y = 0.0

transfer_done   = False   # prevent double-send
transfer_active = False   # visual flag for overlay
at_edge         = False


def get_active_window():
    try:
        hwnd  = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        if title and "SnapShift" not in title:
            return hwnd, title
    except Exception:
        pass
    return None, ""


def get_rect(hwnd):
    try:
        r = win32gui.GetWindowRect(hwnd)
        return r[0], r[1], r[2] - r[0], r[3] - r[1]
    except Exception:
        return 0, 0, 500, 300


def move_window(hwnd, x, y):
    try:
        _, _, w, h = get_rect(hwnd)
        win32gui.MoveWindow(hwnd, int(x), int(y), w, h, True)
    except Exception:
        pass


def find_file(title):
    """Return first file in outbox whose name fuzzy-matches window title."""
    os.makedirs(TRANSFER_DIR, exist_ok=True)
    for fname in os.listdir(TRANSFER_DIR):
        for word in title.split():
            if len(word) > 3 and word.lower() in fname.lower():
                return os.path.join(TRANSFER_DIR, fname)
    # fallback: first file in outbox
    all_files = [
        os.path.join(TRANSFER_DIR, f)
        for f in os.listdir(TRANSFER_DIR)
        if os.path.isfile(os.path.join(TRANSFER_DIR, f))
    ]
    return all_files[0] if all_files else None


def udp_notify_s2(msg):
    """Send short control message to System 2 over UDP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(msg.encode(), (SYSTEM2_IP, SYSTEM2_UDP))
        s.close()
    except Exception as e:
        print(f"[S1] Notify error: {e}")


def tcp_send_file(filepath):
    """Upload file to System 2 over TCP with progress."""
    global transfer_active, transfer_done
    try:
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        print(f"[S1] Connecting to {SYSTEM2_IP}:{SYSTEM2_TCP} ...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((SYSTEM2_IP, SYSTEM2_TCP))

        header = f"HANDOFF|{filename}|{filesize}\n"
        sock.sendall(header.encode())

        sent = 0
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                sock.sendall(chunk)
                sent += len(chunk)
                pct = int(sent / filesize * 100)
                print(f"\r[S1] Uploading... {pct}%", end="", flush=True)

        sock.close()
        print(f"\n[S1] âœ… Transfer complete: {filename}")
        transfer_done   = True
        transfer_active = False

    except Exception as e:
        print(f"\n[S1] âŒ Transfer failed: {e}")
        transfer_active = False


def do_transfer():
    """Trigger file transfer in background thread."""
    global transfer_active, grabbed_file
    if grabbed_file and not transfer_done:
        transfer_active = True
        udp_notify_s2(f"INCOMING|{grabbed_title}|{os.path.basename(grabbed_file)}")
        threading.Thread(target=tcp_send_file, args=(grabbed_file,), daemon=True).start()


def reset_all():
    global grabbed_hwnd, grabbed_title, grabbed_file
    global is_grabbed, transfer_done, transfer_active, at_edge
    is_grabbed      = False
    transfer_done   = False
    transfer_active = False
    at_edge         = False
    grabbed_hwnd    = None
    grabbed_title   = ""
    grabbed_file    = None
    print("[S1] RESET")


def udp_listener():
    global grabbed_hwnd, grabbed_title, grabbed_file
    global is_grabbed, gyro_z, gyro_y, drag_x, drag_y
    global transfer_done, at_edge

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((MY_IP, UDP_PORT))
    sock.settimeout(0.1)
    print(f"[S1] Listening on UDP {UDP_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(1024)
            msg = data.decode("utf-8").strip()

            # â”€â”€ Button 1 â†’ SELECT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if msg == "SELECT":
                hwnd, title = get_active_window()
                if hwnd:
                    grabbed_hwnd  = hwnd
                    grabbed_title = title
                    is_grabbed    = True
                    transfer_done = False
                    drag_x, drag_y, _, _ = get_rect(hwnd)
                    grabbed_file  = find_file(title)
                    print(f"[S1] âœ… Grabbed: {grabbed_title}")
                    print(f"[S1]    File   : {grabbed_file}")
                    print(f"[S1]    Pos    : ({drag_x:.0f}, {drag_y:.0f})")
                else:
                    print("[S1] No window found â€” click your target window first")

            # â”€â”€ Button 2 â†’ RELEASE or SEND â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            elif msg == "RELEASE":
                if is_grabbed:
                    if at_edge and not transfer_done:
                        # === EDGE: TRANSFER TO SYSTEM 2 ===
                        print("[S1] ðŸ“¤ BTN2 at edge â†’ SENDING FILE TO SYSTEM 2")
                        do_transfer()
                        is_grabbed = False
                    else:
                        # === NOT AT EDGE: just drop in place ===
                        is_grabbed = False
                        print(f"[S1] Dropped in place at ({drag_x:.0f}, {drag_y:.0f})")
                else:
                    print("[S1] Nothing grabbed")

            # â”€â”€ Button 3 â†’ RESET â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            elif msg == "RESET":
                reset_all()
                # also notify System 2 to reset
                udp_notify_s2("RESET")

            # â”€â”€ MOTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            elif msg.startswith("MOTION:"):
                parts = msg.split(":")
                if len(parts) == 3:
                    gyro_z = float(parts[1])
                    gyro_y = float(parts[2])

        except socket.timeout:
            pass
        except Exception as e:
            print(f"[S1] UDP error: {e}")


def window_mover():
    global drag_x, drag_y, at_edge

    while True:
        if is_grabbed and grabbed_hwnd:
            dx = gyro_z * MOVE_SENS
            dy = gyro_y * MOVE_SENS
            if abs(dx) < 1.5: dx = 0
            if abs(dy) < 1.5: dy = 0

            drag_x += dx
            drag_y += dy

            # allow going past right edge for visual slide-off effect
            drag_x = max(-200, min(SCREEN_W + 250, drag_x))
            drag_y = max(0, min(SCREEN_H - 80, drag_y))

            move_window(grabbed_hwnd, drag_x, drag_y)

            # edge detection
            at_edge = drag_x >= EDGE_THRESH

            # live notify System 2 overlay when approaching edge
            if drag_x >= SCREEN_W - 280 and not transfer_done:
                udp_notify_s2(f"APPROACHING|{grabbed_title}")

        time.sleep(0.016)


def run_overlay():
    pygame.init()
    screen = pygame.display.set_mode((660, 68), pygame.NOFRAME)
    pygame.display.set_caption("SnapShift S1")
    clock = pygame.time.Clock()
    font  = pygame.font.SysFont("Segoe UI", 17)
    font_sm = pygame.font.SysFont("Segoe UI", 13)

    hwnd_bar = pygame.display.get_wm_info()["window"]
    ctypes.windll.user32.SetWindowPos(hwnd_bar, -1, 10, 10, 0, 0, 0x0001)

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

        screen.fill((12, 12, 20))

        if transfer_active:
            # uploading
            bar_color = (30, 80, 200)
            pygame.draw.rect(screen, bar_color, (0, 0, 660, 68))
            txt = font.render(
                f"ðŸ“¤  Uploading â†’ System 2 ...  {grabbed_title[:30]}",
                True, (180, 220, 255)
            )
            hint = font_sm.render("Transfer in progress ...", True, (120, 160, 220))

        elif transfer_done:
            pygame.draw.rect(screen, (10, 50, 20), (0, 0, 660, 68))
            txt = font.render(
                f"âœ…  Sent: {grabbed_title[:35]}  â†’  System 2",
                True, (80, 220, 100)
            )
            hint = font_sm.render("BTN3 = Reset  |  BTN1 = Grab another", True, (60, 160, 80))

        elif is_grabbed and at_edge:
            # at edge, ready to send
            pygame.draw.rect(screen, (80, 50, 10), (0, 0, 660, 68))
            txt = font.render(
                f"â†’ EDGE  {grabbed_title[:30]}  |  BTN2 = SEND TO SYSTEM 2",
                True, (255, 190, 50)
            )
            hint = font_sm.render("Keep wand tilted right, press BTN2 to fire transfer", True, (200, 160, 60))

        elif is_grabbed:
            txt = font.render(
                f"[MOVING]  {grabbed_title[:38]}  |  BTN2=Drop  BTN3=Reset",
                True, (255, 170, 30)
            )
            hint = font_sm.render(
                f"Pos: ({drag_x:.0f}, {drag_y:.0f})  |  Tilt right to reach edge",
                True, (160, 120, 30)
            )

        else:
            txt = font.render(
                "SnapShift S1  |  Click a window â†’ Press BTN1 to grab",
                True, (80, 210, 100)
            )
            hint = font_sm.render("BTN2 = Send/Drop  |  BTN3 = Reset", True, (60, 140, 80))

        screen.blit(txt,  (10, 8))
        screen.blit(hint, (10, 46))
        pygame.display.flip()
        clock.tick(30)


if __name__ == "__main__":
    print("=" * 48)
    print("  SnapShift System 1 â€” Sender")
    print("  BTN1 = Grab  |  BTN2 = Send/Drop  |  BTN3 = Reset")
    print(f"  Outbox : {TRANSFER_DIR}")
    print(f"  Target : {SYSTEM2_IP}:{SYSTEM2_TCP}")
    print("=" * 48)

    os.makedirs(TRANSFER_DIR, exist_ok=True)

    threading.Thread(target=udp_listener, daemon=True).start()
    threading.Thread(target=window_mover, daemon=True).start()
    run_overlay()