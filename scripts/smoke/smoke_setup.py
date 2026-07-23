import sys, os, requests, time, subprocess

BASE = "http://localhost:8000"
ok = []; fail = []
def check(label, cond):
    if cond: print("  OK  ", label); ok.append(label)
    else:    print("  FAIL", label); fail.append(label)

proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen([py,"-m","uvicorn","app.main:app","--port","8000"],
    cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
# Wait until server reports ready
import threading
ready = threading.Event()
def _watch():
    for line in srv.stdout:
        if b"Application startup complete" in line:
            ready.set(); return
threading.Thread(target=_watch, daemon=True).start()
ready.wait(timeout=20)

try:
    # 1. Setup page accessible with NO accounts
    r = requests.get(BASE+"/superadmin/setup")
    check("Setup accessible (0 accounts)", r.status_code == 200)
    check("First-time wording", "First-time setup" in r.text)
    check("No private-URL warning yet", "Keep this URL private" not in r.text)

    # 2. Login page shows hint when no accounts
    r = requests.get(BASE+"/superadmin/login")
    check("Login shows 'no accounts' hint", "Create the first Super Admin" in r.text)

    # 3. Create first SA via setup
    s1 = requests.Session()
    r = s1.post(BASE+"/superadmin/setup",
                data={"name":"My Admin","email":"admin@mycompany.com",
                      "password":"mypass123","confirm":"mypass123"},
                allow_redirects=True)
    check("First SA created -> dashboard", "Platform Overview" in r.text)

    # 4. Setup page STILL accessible after account exists
    r = requests.get(BASE+"/superadmin/setup")
    check("Setup still accessible (1 account)", r.status_code == 200)
    check("Private-URL warning shown", "Keep this URL private" in r.text)
    check("Shows accounts-exist count", "already exist" in r.text)
    check("Form still present", 'name="email"' in r.text)

    # 5. Create second SA via setup (while first is logged in elsewhere)
    r = requests.post(BASE+"/superadmin/setup",
                      data={"name":"Second Admin","email":"second@mycompany.com",
                            "password":"pass456","confirm":"pass456"},
                      allow_redirects=True)
    check("Second SA created via setup", "Platform Overview" in r.text)

    # 6. Duplicate email rejected
    r = requests.post(BASE+"/superadmin/setup",
                      data={"name":"Dup","email":"admin@mycompany.com",
                            "password":"pass123","confirm":"pass123"},
                      allow_redirects=True)
    check("Duplicate email rejected", "already exists" in r.text)

    # 7. Password mismatch rejected
    r = requests.post(BASE+"/superadmin/setup",
                      data={"name":"X","email":"x@sa.io",
                            "password":"aaa","confirm":"bbb"},
                      allow_redirects=True)
    check("Mismatch rejected", "do not match" in r.text)

    # 8. Both accounts can log in
    for email, pwd in [("admin@mycompany.com","mypass123"),("second@mycompany.com","pass456")]:
        sx = requests.Session()
        r = sx.post(BASE+"/superadmin/login", data={"email":email,"password":pwd}, allow_redirects=True)
        check(f"{email} can login", "Platform Overview" in r.text)

    print(f"\nPASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("FAILED:", fail)
finally:
    srv.terminate(); srv.wait()

sys.exit(0 if not fail else 1)
