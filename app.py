from flask import Flask, render_template, request, redirect, Response, jsonify, session
import cv2
from datetime import datetime
import sqlite3
import os
import numpy as np

app = Flask(__name__)
app.secret_key = "smartattend_secret_2024"

current_subject = "general"
current_department = "general"
# ================= DATABASE =================
def connect_db():
    return sqlite3.connect("smart_attendance.db")


def init_db():
    conn = sqlite3.connect("smart_attendance.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        reg_no TEXT UNIQUE,
        department TEXT,
        class TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS faculty (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        faculty_id TEXT,
        name TEXT,
        password TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        name TEXT,
        department TEXT,
        class TEXT,
        subject TEXT,
        date TEXT,
        time TEXT,
        status TEXT
    )
    """)

    cursor.execute("""
    INSERT OR IGNORE INTO admins(username,password)
    VALUES('admin','admin123')
    """)

    conn.commit()
    conn.close()
# ================= TRAIN FACE MODEL =================
def train_model():
    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")

    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name, reg_no, department, class FROM students")
    students = cursor.fetchall()
    db.close()

    if not students:
        print("❌ No students in DB")
        return None, {}

    student_map = {s[2]: s for s in students}
    faces_data = []
    labels = []
    label_map = {}
    label_counter = 0

    image_folder = "images"
    if not os.path.exists(image_folder):
        print("❌ No images folder")
        return None, {}

    reg_no_files = {}
    for filename in sorted(os.listdir(image_folder)):
        if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            continue
        base = os.path.splitext(filename)[0]
        reg_no = base.rsplit('_', 1)[0] if '_' in base else base
        if reg_no not in student_map:
            continue
        if reg_no not in reg_no_files:
            reg_no_files[reg_no] = []
        reg_no_files[reg_no].append(filename)

    for reg_no, files in sorted(reg_no_files.items()):
        student = student_map[reg_no]
        added = 0
        for filename in files:
            img_path = os.path.join(image_folder, filename)
            img = cv2.imread(img_path)
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, 1.3, 5)
            if len(detected) == 0:
                continue
            x, y, w, h = detected[0]
            face_roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
            faces_data.append(face_roi)
            labels.append(label_counter)
            added += 1

        if added > 0:
            label_map[label_counter] = student
            print(f"🏷️ Label {label_counter} → {reg_no} ({student[1]}) — {added} photo(s)")
            label_counter += 1

    if len(faces_data) == 0:
        print("❌ No valid face data")
        return None, {}

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces_data, np.array(labels))
    print(f"✅ Trained: {label_counter} student(s), {len(faces_data)} total faces")
    return recognizer, label_map

# ================= HOME — protected =================
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect('/login')   # ✅ redirect to login if not logged in
    return render_template("index.html")

# ================= LOGIN PAGE =================
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Already logged in → go home
    if session.get('logged_in'):
        return redirect('/')

    if request.method == 'GET':
        return render_template('login.html')

    # POST — comes as JSON from the login form JS
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role     = data.get('role', 'admin')

    db = connect_db()
    cursor = db.cursor()

    if role == 'admin':
        cursor.execute(
            "SELECT * FROM admins WHERE username=? AND password=?",
            (username, password)
        )
        user = cursor.fetchone()
        db.close()
        if user:
            session['logged_in'] = True
            session['role']      = 'admin'
            session['name']      = username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid admin credentials.'})

    elif role == 'faculty':
        cursor.execute(
            "SELECT * FROM faculty WHERE faculty_id=%s AND password=%s",
            (username, password)
        )
        user = cursor.fetchone()
        db.close()
        if user:
            session['logged_in'] = True
            session['role']      = 'faculty'
            session['name']      = user[1] if len(user) > 1 else username
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'message': 'Invalid faculty credentials.'})

    elif role == 'student':
        cursor.execute(
            "SELECT * FROM students WHERE reg_no=%s",
            (username,)
        )
        user = cursor.fetchone()
        db.close()
        if user:
            session['logged_in'] = True
            session['role']      = 'student'
            session['name']      = user[1]
            session['reg_no']    = username
            return jsonify({'success': True, 'redirect': '/student_dashboard'})
        return jsonify({'success': False, 'message': 'Student ID not found.'})

    db.close()
    return jsonify({'success': False, 'message': 'Invalid credentials.'})

# ================= LOGOUT =================
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ================= STUDENT DASHBOARD =================
@app.route('/student_dashboard')
def student_dashboard():
    if not session.get('logged_in') or session.get('role') != 'student':
        return redirect('/login')
    reg_no = session.get('reg_no')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT subject, date, time, status FROM attendance
        WHERE student_id = (SELECT id FROM students WHERE reg_no = %s)
        ORDER BY date DESC, time DESC
    """, (reg_no,))
    records = cursor.fetchall()
    cursor.execute("""
        SELECT subject, COUNT(*) FROM attendance
        WHERE student_id = (SELECT id FROM students WHERE reg_no = %s)
        GROUP BY subject
    """, (reg_no,))
    subject_stats = cursor.fetchall()
    db.close()
    return render_template("student_dashboard.html",
                           name=session.get('name'),
                           reg_no=reg_no,
                           records=records,
                           subject_stats=subject_stats)

# ================= CLASS SESSION =================
@app.route('/session', methods=['GET', 'POST'])
def class_session():
    if not session.get('logged_in'):
        return redirect('/login')
    if request.method == 'POST':
        subject    = request.form.get('subject', 'general').strip().lower()
        department = request.form.get('department', 'general').strip().lower()
        return redirect(f'/camera?subject={subject}&department={department}')
    return render_template("class_session.html")

# ================= REGISTER =================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name       = request.form['name']
        reg_no     = request.form['reg_no']
        department = request.form['department']
        class_name = request.form['class_name']

        if not os.path.exists("images"):
            os.makedirs("images")

        face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(0)

        saved       = 0
        attempts    = 0
        max_attempts = 200

        print(f"📸 Starting capture for {name} ({reg_no})...")

        while saved < 20 and attempts < max_attempts:
            success, frame = cap.read()
            attempts += 1
            if not success:
                continue

            gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, 1.3, 5)

            if len(detected) == 0:
                continue

            photo_path = f"images/{reg_no}_{saved + 1}.jpg"
            cv2.imwrite(photo_path, frame)
            saved += 1
            print(f"📸 Captured {saved}/20 for {name}")
            cv2.waitKey(200)

        cap.release()

        if saved == 0:
            return "❌ No face detected during registration. Make sure your face is visible to the webcam."

        print(f"✅ Registered {name} with {saved} photos")

        db = connect_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO students (name, reg_no, department, class) VALUES (%s,%s,%s,%s)",
            (name, reg_no, department, class_name)
        )
        db.commit()
        db.close()
        return redirect('/')

    return render_template("register.html")

# ================= CAMERA PAGE =================
@app.route('/camera')
def camera():
    global current_subject, current_department
    if not session.get('logged_in'):
        return redirect('/login')
    current_subject    = request.args.get('subject', 'general').strip().lower()
    current_department = request.args.get('department', 'general').strip().lower()
    return render_template("camera.html",
                           subject=current_subject,
                           department=current_department)

# ================= VIDEO STREAM =================
def generate_frames():
    cap = cv2.VideoCapture(0)
    while True:
        success, frame = cap.read()
        if not success:
            break
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# ================= MARK ATTENDANCE =================
@app.route('/mark_attendance')
def mark_attendance():
    global current_subject, current_department

    if not session.get('logged_in'):
        return redirect('/login')

    if request.args.get('subject'):
        current_subject    = request.args.get('subject').strip().lower()
    if request.args.get('department'):
        current_department = request.args.get('department').strip().lower()

    print(f"📚 Subject: '{current_subject}' | Dept: '{current_department}'")

    recognizer, label_map = train_model()
    if recognizer is None:
        return "❌ Could not train model. Please register students first."

    cap          = cv2.VideoCapture(0)
    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")

    if face_cascade.empty():
        cap.release()
        return "❌ Haarcascade file missing"

    best_frame      = None
    best_face_count = 0

    for _ in range(15):
        success, frame = cap.read()
        if not success:
            continue
        gray_test  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces_test = face_cascade.detectMultiScale(gray_test, 1.3, 5)
        if len(faces_test) > best_face_count:
            best_face_count = len(faces_test)
            best_frame      = frame

    cap.release()

    if best_frame is None or best_face_count == 0:
        return "⚠️ No face detected. Please position your face clearly and try again."

    gray  = cv2.cvtColor(best_frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    print(f"📷 Detected {len(faces)} face(s)")

    db     = connect_db()
    cursor = db.cursor()

    CONFIDENCE_THRESHOLD = 60
    marked_names  = []
    skipped_names = []

    for (x, y, w, h) in faces:
        face_roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        label, confidence = recognizer.predict(face_roi)

        print(f"🔍 Label: {label}, Confidence: {confidence:.2f}")

        if confidence > CONFIDENCE_THRESHOLD:
            print(f"⚠️ Confidence {confidence:.2f} > {CONFIDENCE_THRESHOLD} — not recognized")
            continue

        if label not in label_map:
            continue

        student    = label_map[label]
        student_id, name, reg_no, dept, cls = student
        now        = datetime.now()

        try:
            cursor.execute(
                "SELECT id FROM attendance WHERE student_id=%s AND LOWER(subject)=%s AND date=%s",
                (student_id, current_subject, now.date())
            )
            already = cursor.fetchone()

            if already:
                print(f"⚠️ {name} already marked for '{current_subject}' today")
                skipped_names.append(name)
            else:
                cursor.execute(
                    "INSERT INTO attendance "
                    "(student_id, name, department, class, subject, date, time, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (student_id, name, dept, cls, current_subject,
                     now.date(), now.time(), "Present")
                )
                db.commit()
                marked_names.append(name)
                print(f"✅ Marked: {name} for '{current_subject}'")

        except Exception as e:
            print(f"❌ DB ERROR for {name}: {e}")

    db.close()

    if not marked_names and not skipped_names:
        return (
            f"⚠️ Face detected but not recognized. "
            f"Confidence was above {CONFIDENCE_THRESHOLD}. "
            f"Please delete old images and re-register in good lighting."
        )

    return redirect('/attendance')

# ================= VIDEO FEED =================
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ================= ATTENDANCE =================
@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM attendance")
    data = cursor.fetchall()
    db.close()
    return render_template("attendance.html", data=data)

# ================= DASHBOARD =================
@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("SELECT department, COUNT(*) FROM attendance GROUP BY department")
    dept_data = cursor.fetchall()
    cursor.execute("SELECT subject, COUNT(*) FROM attendance GROUP BY subject")
    subject_data = cursor.fetchall()
    db.close()
    return render_template("dashboard.html",
                           dept_data=dept_data,
                           subject_data=subject_data)

# ================= DELETE =================
@app.route('/delete/<int:id>')
def delete(id):
    if not session.get('logged_in'):
        return redirect('/login')
    db = connect_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM attendance WHERE id=%s", (id,))
    db.commit()
    db.close()
    return redirect('/attendance')

# ================= RUN =================
if __name__ == "__main__":
    init_db()
    app.run(debug=True)