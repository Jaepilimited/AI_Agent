"""Windows desktop notification utility.

Usage:
    from app.core.notify import notify
    notify("테스트 완료", "55/55 OK, 0 WARN, 0 FAIL")
"""
import winsound
import threading


def notify(title: str = "AI Agent", message: str = "작업이 완료되었습니다.", sound: bool = True):
    """Send a Windows 10 toast notification with optional sound.

    Args:
        title: Notification title.
        message: Notification body text.
        sound: Whether to play a notification sound.
    """
    # Sound alert (non-blocking)
    if sound:
        threading.Thread(target=_play_sound, daemon=True).start()

    # Toast notification
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(
            title,
            message,
            duration=10,
            threaded=True,
        )
    except Exception:
        # Fallback: just print to console
        print(f"\n{'=' * 50}")
        print(f"  [NOTIFICATION] {title}")
        print(f"  {message}")
        print(f"{'=' * 50}\n")


def _play_sound():
    """Play a notification sound."""
    try:
        winsound.MessageBeep(winsound.MB_ICONINFORMATION)
    except Exception:
        pass
