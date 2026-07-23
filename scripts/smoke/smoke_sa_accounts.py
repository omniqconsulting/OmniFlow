"""SA Account Management Smoke Test"""
import sys, os, requests, time, subprocess, re

BASE = "http://localhost:8000"
ok = []; fail = []

def check(label, cond):
    if cond: print("  OK  ", label); ok.append(label)
    else:    print("  FAIL", label); fail.append(label)

proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
py   = os.path.join(proj, "venv", "Scripts", "python.exe")
srv  = subprocess.Popen(
    [py, "-m", "uvicorn", "app.main:app", "--port", "8000"],
    cwd=proj, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

try:
    s = requests.Session()
    r = s.post(BASE+"/superadmin/setup",
               data={"name":"Admin One","email":"one@sa.io","password":"pass123","confirm":"pass123"},
               allow_redirects=True)
    check("Setup -> dashboard", "Platform Overview" in r.text)
    check("sa_token set", "sa_token" in s.cookies)

    # --- Profile page ---
    r = s.get(BASE+"/superadmin/profile")
    check("Profile 200", r.status_code == 200)
    check("Email shown", "one@sa.io" in r.text)
    check("Credential reminder box", "/superadmin/login" in r.text)
    check("Last Login row", "Last Login" in r.text)

    # Update name
    r = s.post(BASE+"/superadmin/profile/name",
               data={"name":"Admin One Updated"}, allow_redirects=True)
    check("Name updated", "Admin One Updated" in r.text)

    # --- SA accounts page ---
    r = s.get(BASE+"/superadmin/admins")
    check("Admins page 200", r.status_code == 200)
    check("Own account visible", "Admin One Updated" in r.text)
    check("YOU badge", "YOU" in r.text)
    check("Security notes", "bcrypt" in r.text)

    # Add second SA
    r = s.post(BASE+"/superadmin/admins/new",
               data={"name":"Admin Two","email":"two@sa.io","password":"pass456"},
               allow_redirects=True)
    check("Second SA created", "Admin Two" in r.text)

    # Deactivate second SA
    tids = re.findall(r"/superadmin/admins/([a-f0-9\-]{36})/deactivate", r.text)
    check("Deactivate button found", len(tids) > 0)
    if tids:
        tid2 = tids[0]
        r2 = s.post(BASE+"/superadmin/admins/"+tid2+"/deactivate", allow_redirects=True)
        check("Deactivated", "deactivated" in r2.url or "Inactive" in r2.text)
        # Admin Two cannot login
        s3 = requests.Session()
        r3 = s3.post(BASE+"/superadmin/login",
                     data={"email":"two@sa.io","password":"pass456"}, allow_redirects=True)
        check("Deactivated SA cannot login", "Platform Overview" not in r3.text)
        # Reactivate
        r4 = s.post(BASE+"/superadmin/admins/"+tid2+"/activate", allow_redirects=True)
        check("Reactivated", "activated" in r4.url or "Active" in r4.text)
        s3b = requests.Session()
        r3b = s3b.post(BASE+"/superadmin/login",
                       data={"email":"two@sa.io","password":"pass456"}, allow_redirects=True)
        check("Reactivated SA can login", "Platform Overview" in r3b.text)

    # --- Change password: wrong current ---
    r = s.post(BASE+"/superadmin/profile/password",
               data={"current_password":"WRONG","new_password":"newpass99","confirm_password":"newpass99"},
               allow_redirects=True)
    check("Wrong password rejected", "wrong_current" in r.url or "incorrect" in r.text.lower())

    # Change password: mismatch
    r = s.post(BASE+"/superadmin/profile/password",
               data={"current_password":"pass123","new_password":"aaa111","confirm_password":"bbb222"},
               allow_redirects=True)
    check("Mismatch rejected", "mismatch" in r.url or "match" in r.text.lower())

    # Change password: correct
    r = s.post(BASE+"/superadmin/profile/password",
               data={"current_password":"pass123","new_password":"newpass99","confirm_password":"newpass99"},
               allow_redirects=True)
    check("Password changed -> login page", "Sign In" in r.text)

    # Login with new password
    s2 = requests.Session()
    r = s2.post(BASE+"/superadmin/login",
                data={"email":"one@sa.io","password":"newpass99"}, allow_redirects=True)
    check("Login with new password works", "Platform Overview" in r.text)

    # Old password rejected
    s_old = requests.Session()
    r = s_old.post(BASE+"/superadmin/login",
                   data={"email":"one@sa.io","password":"pass123"}, allow_redirects=True)
    check("Old password rejected", "Platform Overview" not in r.text)

    print(f"\nPASSED {len(ok)}/{len(ok)+len(fail)}")
    if fail: print("FAILED:", fail)

finally:
    srv.terminate()
    srv.wait()

sys.exit(0 if not fail else 1)
