r"""
run_waitress.py

PURPOSE
-------
This is the *production launcher* for your Flask app using Waitress.

Key behavior:
1) Imports your Flask app module (app.py)
2) Calls app.startup() to do a BLOCKING preload of all required datasets
   - This must finish successfully BEFORE the web server starts listening
   - If preload fails (missing files/DB connection/etc.), the process exits and
     Waitress never starts, so no users can hit the site with incomplete data.
3) Starts Waitress and begins accepting HTTP requests.

HOW TO LAUNCH (Windows / PowerShell)
------------------------------------
1) Open a terminal in your project folder, for example:
   cd C:/Users/mowlic/PRCAPP_TESTV04

2) Use your WRVUENV python to run this file (recommended):
   & "C:\Users\mowlic\.conda\envs\WRVUENV\python.exe" run_waitress.py

   (If you have set python as default already, you can simply run:)
   python run_waitress.py

3) Open in browser:
   http://localhost:8000

STOPPING THE SERVER
-------------------
Press CTRL + C in the terminal where it is running.
"""

from waitress import serve
import app

if __name__ == "__main__":
    # 1) BLOCKING STARTUP STEP
    # ------------------------
    # This loads all datasets into memory and starts the 05:00 AM refresh thread.
    # IMPORTANT: Waitress is NOT listening on port 8000 yet.
    # If startup() raises an exception, this script exits and the web server never starts.
    print(">>> Starting app startup() preload (blocking). Waitress is NOT listening yet...")
    app.startup()
    print(">>> Preload successful. Starting Waitress listener on http://localhost:8000 ...")

    # 2) START WEB SERVER
    # -------------------
    # Only after preload succeeds do we start listening/accepting HTTP requests.
    # host="0.0.0.0" allows access from other machines on the network (if firewall allows).
    # If you want *only* local machine access, change host to "127.0.0.1".
    serve(app.app, host="0.0.0.0", port=8000, threads=8)
