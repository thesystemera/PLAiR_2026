import asyncio
import platform
import sys

ENABLE_CONSOLE_TEXT_SELECTION = False

if platform.system() == "Windows":
    import ctypes
    kernel32 = ctypes.windll.kernel32

    if not ENABLE_CONSOLE_TEXT_SELECTION:
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 128)
        print("[STARTUP] Disabled Windows Quick Edit Mode (no more console freezing!)", file=sys.stderr)
    else:
        print("[STARTUP] Quick Edit Mode ENABLED - you can select text but console may freeze if clicked", file=sys.stderr)

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    print("[STARTUP] Forced WindowsProactorEventLoopPolicy", file=sys.stderr)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="asyncio"
    )
