import subprocess


def notify(message: str, title: str = "Lucy Note Manager"):
    """
    Send desktop notification via notify-send.
    Fails silently if notify-send is unavailable.
    """
    try:
        subprocess.run(
            ["notify-send", title, message],
            check=False,
        )
    except Exception:
        pass
