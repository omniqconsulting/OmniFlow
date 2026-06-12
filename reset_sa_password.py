"""
Emergency Super Admin Password Reset
Run from the project root: python reset_sa_password.py

Use this if all SA accounts are locked out and you cannot access /superadmin/login.
Requires direct server access.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal, SuperAdmin
from app.superadmin_auth import sa_hash

db = SessionLocal()
admins = db.query(SuperAdmin).all()

if not admins:
    print("No super admin accounts exist.")
    print("Start the server and visit /superadmin/setup to create one.")
    sys.exit(0)

print("\nExisting Super Admin accounts:")
print("-" * 40)
for i, sa in enumerate(admins):
    status = "ACTIVE" if sa.is_active else "INACTIVE"
    print(f"  [{i}]  {sa.name}  <{sa.email}>  [{status}]")

print()
idx = input("Enter the number of the account to reset (or 'new' to create one): ").strip()

if idx.lower() == "new":
    name  = input("Name: ").strip()
    email = input("Email: ").strip()
    pwd   = input("Password (min 6 chars): ").strip()
    if len(pwd) < 6:
        print("Password too short."); sys.exit(1)
    existing = db.query(SuperAdmin).filter(SuperAdmin.email == email).first()
    if existing:
        print(f"Email already used by: {existing.name}")
        sys.exit(1)
    new_sa = SuperAdmin(name=name, email=email, password_hash=sa_hash(pwd), is_active=True)
    db.add(new_sa)
    db.commit()
    print(f"\nNew super admin created: {name} <{email}>")
    print("You can now log in at /superadmin/login")
    sys.exit(0)

try:
    sa = admins[int(idx)]
except (ValueError, IndexError):
    print("Invalid selection."); sys.exit(1)

pwd = input(f"New password for {sa.name} <{sa.email}>: ").strip()
if len(pwd) < 6:
    print("Password too short."); sys.exit(1)

sa.password_hash = sa_hash(pwd)
sa.is_active = True
db.commit()
print(f"\nPassword reset for {sa.name}.")
print("Account is now ACTIVE. Log in at /superadmin/login")
db.close()
