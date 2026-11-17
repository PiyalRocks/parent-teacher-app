import os
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
# MySQL: mysql+pymysql://user:password@localhost/ptcs   (default: local SQLite for quick tests)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "ptcs.db"))
db = SQLAlchemy(app)
lm = LoginManager(app)
lm.login_view = "login"

# ---------------------------------------------------------------- models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="parent")  # admin|teacher|parent

class Classroom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    section = db.Column(db.String(20), default="A")
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    teacher = db.relationship("User")

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classroom.id"))
    classroom = db.relationship("Classroom", backref="students")

class Link(db.Model):  # parent <-> student
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    parent = db.relationship("User")
    student = db.relationship("Student")

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    day = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False)  # Present|Absent|Late
    __table_args__ = (db.UniqueConstraint("student_id", "day"),)

class Skill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)

class Assessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skill.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # 0-100
    note = db.Column(db.String(255))
    day = db.Column(db.Date, default=date.today)
    skill = db.relationship("Skill")

class Activity(db.Model):  # homework / learning activity
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))  # NULL = whole class
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    due = db.Column(db.Date)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    done = db.Column(db.Boolean, default=False)
    comment = db.Column(db.Text)
    teacher_reply = db.Column(db.Text)
    created = db.Column(db.DateTime, default=datetime.now)
    activity = db.relationship("Activity")
    student = db.relationship("Student")

class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    day = db.Column(db.Date)
    published = db.Column(db.Boolean, default=False)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    marks = db.Column(db.Float)
    exam = db.relationship("Exam")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    sent = db.Column(db.DateTime, default=datetime.now)

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text)
    created = db.Column(db.DateTime, default=datetime.now)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.String(255), nullable=False)
    seen = db.Column(db.Boolean, default=False)
    created = db.Column(db.DateTime, default=datetime.now)

# ---------------------------------------------------------------- helpers
@lm.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

def role_required(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **k):
            if current_user.role not in roles:
                abort(403)
            return f(*a, **k)
        return wrapper
    return deco

def notify(user_id, text):
    db.session.add(Notification(user_id=user_id, text=text))

def parents_of_class(class_id):
    return [l.parent_id for l in Link.query.join(Student).filter(Student.class_id == class_id)]

def my_class(cid):
    c = db.get_or_404(Classroom, cid)
    if current_user.role != "admin" and c.teacher_id != current_user.id:
        abort(403)
    return c

def check_access(s):
    """Privacy rule: parents see only linked children; teachers only their classes."""
    r = current_user.role
    if r == "admin":
        return
    if r == "teacher" and s.classroom and s.classroom.teacher_id == current_user.id:
        return
    if r == "parent" and Link.query.filter_by(parent_id=current_user.id, student_id=s.id).first():
        return
    abort(403)

PASS_MARK = 60  # rule: latest score below this = skill gap
SUGGESTIONS = {
    "Literacy": "Read a picture book together for 15 minutes daily and ask the child to retell it.",
    "Numeracy": "Count everyday objects (steps, fruits) and practise simple sorting games.",
    "Motor Skills": "Try drawing, colouring, bead threading and playdough for 10 minutes a day.",
    "Social Skills": "Arrange short group play and practise taking turns and saying please/thank you.",
}

def skill_gaps(sid):
    gaps = []
    for sk in Skill.query.all():
        last = (Assessment.query.filter_by(student_id=sid, skill_id=sk.id)
                .order_by(Assessment.day.desc(), Assessment.id.desc()).first())
        if last and last.score < PASS_MARK:
            gaps.append({"skill": sk.name, "score": last.score,
                         "tip": SUGGESTIONS.get(sk.name, "Practise this skill with short daily activities.")})
    return gaps

def contacts():
    if current_user.role == "parent":
        cids = {l.student.class_id for l in Link.query.filter_by(parent_id=current_user.id)}
        return User.query.join(Classroom, Classroom.teacher_id == User.id).filter(Classroom.id.in_(cids)).distinct().all() if cids else []
    if current_user.role == "teacher":
        ids = set()
        for c in Classroom.query.filter_by(teacher_id=current_user.id):
            ids.update(parents_of_class(c.id))
        return User.query.filter(User.id.in_(ids)).all() if ids else []
    return []

@app.context_processor
def inject():
    n = 0
    if current_user.is_authenticated:
        n = Notification.query.filter_by(user_id=current_user.id, seen=False).count()
    return {"unread": n}

# ---------------------------------------------------------------- auth
@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():  # public sign-up creates PARENT accounts only; admin assigns other roles
    if request.method == "POST":
        f = request.form
        if len(f["password"]) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif User.query.filter_by(email=f["email"].strip().lower()).first():
            flash("That email is already registered.", "danger")
        else:
            db.session.add(User(name=f["name"].strip(), email=f["email"].strip().lower(),
                                password_hash=generate_password_hash(f["password"]), role="parent"))
            db.session.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if u and check_password_hash(u.password_hash, request.form["password"]):
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Wrong password." if u else "No account found for that email.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for({"admin": "admin", "teacher": "teacher_home", "parent": "parent_home"}[current_user.role]))

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form["name"].strip()
        if request.form.get("password"):
            current_user.password_hash = generate_password_hash(request.form["password"])
        db.session.commit()
        flash("Profile saved.", "success")
    return render_template("profile.html")

# ---------------------------------------------------------------- admin
@app.route("/admin")
@role_required("admin")
def admin():
    return render_template("admin.html", users=User.query.all(),
                           teachers=User.query.filter_by(role="teacher").all(),
                           parents=User.query.filter_by(role="parent").all(),
                           classes=Classroom.query.all(), students=Student.query.all(),
                           links=Link.query.all())

@app.route("/admin/<action>", methods=["POST"])
@role_required("admin")
def admin_action(action):
    f = request.form
    try:
        if action == "user":
            db.session.add(User(name=f["name"], email=f["email"].strip().lower(),
                                password_hash=generate_password_hash(f["password"]), role=f["role"]))
        elif action == "role":
            u = db.get_or_404(User, int(f["user_id"]))
            u.role = f["role"]
            notify(u.id, f"Your role is now: {u.role}")
        elif action == "class":
            db.session.add(Classroom(name=f["name"], section=f["section"], teacher_id=int(f["teacher_id"]) or None))
        elif action == "student":
            db.session.add(Student(name=f["name"], class_id=int(f["class_id"])))
        elif action == "link":
            if not Link.query.filter_by(parent_id=f["parent_id"], student_id=f["student_id"]).first():
                db.session.add(Link(parent_id=int(f["parent_id"]), student_id=int(f["student_id"])))
                notify(int(f["parent_id"]), "A student was linked to your account.")
        db.session.commit()
        flash("Saved.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not save: {e}", "danger")
    return redirect(url_for("admin"))

# ---------------------------------------------------------------- teacher
@app.route("/teacher")
@role_required("teacher", "admin")
def teacher_home():
    q = Classroom.query if current_user.role == "admin" else Classroom.query.filter_by(teacher_id=current_user.id)
    return render_template("teacher_home.html", classes=q.all())

@app.route("/teacher/class/<int:cid>")
@role_required("teacher", "admin")
def teacher_class(cid):
    c = my_class(cid)
    day = request.args.get("day", date.today().isoformat())
    att = {a.student_id: a.status for a in Attendance.query.filter_by(day=date.fromisoformat(day))}
    exams = Exam.query.filter_by(class_id=cid).all()
    res = {(r.exam_id, r.student_id): r.marks for r in Result.query.join(Exam).filter(Exam.class_id == cid)}
    fb = Feedback.query.join(Activity).filter(Activity.class_id == cid).order_by(Feedback.created.desc()).all()
    return render_template("teacher_class.html", c=c, day=day, att=att, exams=exams, res=res,
                           skills=Skill.query.all(), feedback=fb,
                           activities=Activity.query.filter_by(class_id=cid).all(),
                           announcements=Announcement.query.filter_by(class_id=cid).order_by(Announcement.created.desc()).all())

@app.route("/teacher/class/<int:cid>/<action>", methods=["POST"])
@role_required("teacher", "admin")
def teacher_action(cid, action):
    c = my_class(cid)
    f = request.form
    try:
        if action == "attendance":
            day = date.fromisoformat(f["day"])
            for s in c.students:
                st = f.get(f"s{s.id}", "Present")
                a = Attendance.query.filter_by(student_id=s.id, day=day).first()
                if a: a.status = st
                else: db.session.add(Attendance(student_id=s.id, day=day, status=st))
                for p in Link.query.filter_by(student_id=s.id):
                    notify(p.parent_id, f"{s.name} was marked {st} on {day}.")
        elif action == "assess":
            s = db.get_or_404(Student, int(f["student_id"]))
            score = int(f["score"])
            if s.class_id != cid or not 0 <= score <= 100:
                raise ValueError("Invalid student or score (0-100).")
            db.session.add(Assessment(student_id=s.id, skill_id=int(f["skill_id"]), score=score, note=f["note"]))
            for p in Link.query.filter_by(student_id=s.id):
                notify(p.parent_id, f"New assessment recorded for {s.name}.")
        elif action == "activity":
            sid = int(f["student_id"]) or None
            a = Activity(class_id=cid, student_id=sid, title=f["title"], description=f["description"],
                         due=date.fromisoformat(f["due"]) if f["due"] else None)
            db.session.add(a)
            targets = [l.parent_id for l in Link.query.filter_by(student_id=sid)] if sid else parents_of_class(cid)
            for p in targets: notify(p, f"New activity: {a.title}")
        elif action == "exam":
            db.session.add(Exam(class_id=cid, title=f["title"], day=date.fromisoformat(f["day"]) if f["day"] else None))
            for p in parents_of_class(cid): notify(p, f"New exam scheduled: {f['title']}")
        elif action == "announce":
            db.session.add(Announcement(class_id=cid, title=f["title"], body=f["body"]))
            for p in parents_of_class(cid): notify(p, f"Announcement: {f['title']}")
        elif action == "results":
            e = db.get_or_404(Exam, int(f["exam_id"]))
            if e.class_id != cid: abort(403)
            for s in c.students:
                v = f.get(f"m{s.id}")
                if v == "" or v is None: continue
                r = Result.query.filter_by(exam_id=e.id, student_id=s.id).first()
                if r: r.marks = float(v)
                else: db.session.add(Result(exam_id=e.id, student_id=s.id, marks=float(v)))
            if f.get("publish") and not e.published:
                e.published = True
                for p in parents_of_class(cid): notify(p, f"Results published: {e.title}")
        elif action == "reply":
            fb = db.get_or_404(Feedback, int(f["feedback_id"]))
            if fb.activity.class_id != cid: abort(403)
            fb.teacher_reply = f["reply"]
            for p in Link.query.filter_by(student_id=fb.student_id):
                notify(p.parent_id, f"Teacher replied on '{fb.activity.title}'.")
        db.session.commit()
        flash("Saved.", "success")
    except (ValueError, KeyError) as e:
        db.session.rollback()
        flash(f"Invalid input: {e}", "danger")
    return redirect(request.referrer or url_for("teacher_class", cid=cid))

# ---------------------------------------------------------------- parent + shared child view
@app.route("/parent")
@role_required("parent")
def parent_home():
    kids = [l.student for l in Link.query.filter_by(parent_id=current_user.id)]
    anns = []
    for k in kids:
        anns += Announcement.query.filter_by(class_id=k.class_id).all()
    return render_template("parent_home.html", kids=kids, anns=sorted(anns, key=lambda a: a.created, reverse=True))

@app.route("/child/<int:sid>")
@login_required
def child(sid):
    s = db.get_or_404(Student, sid)
    check_access(s)
    q = Assessment.query.filter_by(student_id=sid)  # report filters: skill + period
    skill, start, end = request.args.get("skill", type=int), request.args.get("start"), request.args.get("end")
    if skill: q = q.filter_by(skill_id=skill)
    if start: q = q.filter(Assessment.day >= date.fromisoformat(start))
    if end: q = q.filter(Assessment.day <= date.fromisoformat(end))
    rows = q.order_by(Assessment.day).all()
    week = date.today() - timedelta(days=7)
    att = Attendance.query.filter_by(student_id=sid).order_by(Attendance.day.desc()).limit(30).all()
    wk_att = [a for a in att if a.day >= week]
    acts = Activity.query.filter(Activity.class_id == s.class_id,
                                 (Activity.student_id.is_(None)) | (Activity.student_id == sid)).all()
    fbs = {f.activity_id: f for f in Feedback.query.filter_by(student_id=sid)}
    results = Result.query.join(Exam).filter(Result.student_id == sid, Exam.published.is_(True)).all()
    summary = {"present": sum(a.status == "Present" for a in wk_att), "days": len(wk_att),
               "assessments": sum(r.day >= week for r in rows),
               "done": sum(1 for f in fbs.values() if f.done and f.created.date() >= week)}
    return render_template("child.html", s=s, rows=rows, att=att, acts=acts, fbs=fbs, results=results,
                           gaps=skill_gaps(sid), skills=Skill.query.all(), summary=summary,
                           sel=dict(skill=skill, start=start or "", end=end or ""))

@app.route("/child/<int:sid>/feedback/<int:aid>", methods=["POST"])
@role_required("parent")
def give_feedback(sid, aid):
    s = db.get_or_404(Student, sid)
    check_access(s)
    a = db.get_or_404(Activity, aid)
    if a.class_id != s.class_id: abort(403)
    fb = Feedback.query.filter_by(activity_id=aid, student_id=sid).first() or Feedback(activity_id=aid, student_id=sid)
    fb.done = bool(request.form.get("done"))
    fb.comment = request.form.get("comment", "")
    db.session.add(fb)
    if s.classroom.teacher_id:
        notify(s.classroom.teacher_id, f"Feedback on '{a.title}' for {s.name}.")
    db.session.commit()
    flash("Feedback sent.", "success")
    return redirect(url_for("child", sid=sid))

# ---------------------------------------------------------------- messages + notifications
@app.route("/messages")
@role_required("parent", "teacher")
def messages():
    return render_template("messages.html", people=contacts())

@app.route("/messages/<int:uid>", methods=["GET", "POST"])
@role_required("parent", "teacher")
def thread(uid):
    other = db.get_or_404(User, uid)
    if other.id not in [u.id for u in contacts()]: abort(403)
    if request.method == "POST" and request.form["body"].strip():
        db.session.add(Message(sender_id=current_user.id, receiver_id=uid, body=request.form["body"].strip()))
        notify(uid, f"New message from {current_user.name}")
        db.session.commit()
        return redirect(url_for("thread", uid=uid))
    msgs = Message.query.filter(((Message.sender_id == current_user.id) & (Message.receiver_id == uid)) |
                                ((Message.sender_id == uid) & (Message.receiver_id == current_user.id))).order_by(Message.sent).all()
    return render_template("thread.html", other=other, msgs=msgs)

@app.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created.desc()).limit(50).all()
    shown = [(n.text, n.created, n.seen) for n in items]
    for n in items: n.seen = True
    db.session.commit()
    return render_template("notifications.html", items=shown)

@app.errorhandler(403)
def forbidden(e):
    return render_template("base.html", forbidden=True), 403

DEMO = [("Admin", "admin@school.com", "admin123", "admin"),
        ("Ms. Rahman", "teacher@school.com", "teacher123", "teacher"),
        ("Mr. Karim", "parent@school.com", "parent123", "parent")]

def init_db(reset_demo=False):
    """Create tables; add skills and demo data if missing. reset_demo=True resets demo passwords."""
    db.create_all()
    for n in ["Literacy", "Numeracy", "Motor Skills", "Social Skills"]:
        if not Skill.query.filter_by(name=n).first():
            db.session.add(Skill(name=n))
    first_run = User.query.count() == 0
    for name, email, pw, role in DEMO:
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, name=name, role=role, password_hash=generate_password_hash(pw))
            db.session.add(u)
        elif reset_demo:
            u.name, u.role, u.password_hash = name, role, generate_password_hash(pw)
    db.session.commit()
    if first_run:  # sample class, child, assessments, activity
        t = User.query.filter_by(email="teacher@school.com").first()
        p = User.query.filter_by(email="parent@school.com").first()
        c = Classroom(name="Nursery", section="A", teacher_id=t.id)
        db.session.add(c); db.session.flush()
        s1, s2 = Student(name="Ayaan Karim", class_id=c.id), Student(name="Mira Das", class_id=c.id)
        db.session.add_all([s1, s2]); db.session.flush()
        db.session.add(Link(parent_id=p.id, student_id=s1.id))
        for i, (sk, sc) in enumerate([(1, 55), (2, 80), (3, 70), (4, 45)]):
            db.session.add(Assessment(student_id=s1.id, skill_id=sk, score=sc, day=date.today() - timedelta(days=i)))
        db.session.add(Activity(class_id=c.id, title="Count 10 objects at home",
                                description="Count toys or fruits.", due=date.today() + timedelta(days=3)))
        db.session.commit()

with app.app_context():
    init_db()

if __name__ == "__main__":
    print("Using database:", app.config["SQLALCHEMY_DATABASE_URI"])
    print("Demo logins: admin@school.com/admin123  teacher@school.com/teacher123  parent@school.com/parent123")
    app.run(debug=True)
