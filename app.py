from flask import Flask, jsonify, request, render_template_string
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
import uuid

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "medislot.db"
app = Flask(__name__)

TESTS = {
    "Blood Test": {"duration": 20, "rooms": ["Lab 1", "Lab 2"], "staff": ["Lab Team A", "Lab Team B"]},
    "X-Ray": {"duration": 30, "rooms": ["Imaging 1"], "staff": ["Radiology Team"]},
    "ECG": {"duration": 15, "rooms": ["ECG Room 1", "ECG Room 2"], "staff": ["Cardiac Team A", "Cardiac Team B"]},
}
PRIORITIES = {"Routine": 1, "Priority": 2, "Urgent (authorised)": 3}
START_HOUR, END_HOUR = 9, 17
SLOT_STEP = 15

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            test_type TEXT NOT NULL,
            staff TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_code TEXT NOT NULL,
            test_type TEXT NOT NULL,
            priority TEXT NOT NULL,
            requested_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            room TEXT,
            staff TEXT,
            start_time TEXT,
            end_time TEXT,
            created_at TEXT NOT NULL
        );
        """)
        room_count = con.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        if room_count == 0:
            rooms = [
                ("Lab 1", "Blood Test", "Lab Team A"),
                ("Lab 2", "Blood Test", "Lab Team B"),
                ("Imaging 1", "X-Ray", "Radiology Team"),
                ("ECG Room 1", "ECG", "Cardiac Team A"),
                ("ECG Room 2", "ECG", "Cardiac Team B"),
            ]
            con.executemany("INSERT INTO rooms(name,test_type,staff) VALUES(?,?,?)", rooms)
        count = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        if count == 0:
            today = date.today().isoformat()
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            seed = [
                ("PT-1042", "Blood Test", "Routine", today, "Pending", None, None, None, None),
                ("PT-2081", "X-Ray", "Urgent (authorised)", today, "Pending", None, None, None, None),
                ("PT-3194", "ECG", "Priority", today, "Pending", None, None, None, None),
                ("PT-4270", "Blood Test", "Routine", today, "Pending", None, None, None, None),
                ("PT-5516", "ECG", "Routine", tomorrow, "Pending", None, None, None, None),
                ("PT-6823", "X-Ray", "Routine", tomorrow, "Pending", None, None, None, None),
            ]
            now = datetime.now().isoformat(timespec="seconds")
            con.executemany("""INSERT INTO requests
                (patient_code,test_type,priority,requested_date,status,room,staff,start_time,end_time,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", [tuple(x) + (now,) for x in seed])

def request_dict(row):
    return {k: row[k] for k in row.keys()}

def get_requests():
    with connect() as con:
        rows = con.execute("""SELECT * FROM requests
          WHERE status != 'Cancelled'
          ORDER BY requested_date ASC,
          CASE priority WHEN 'Urgent (authorised)' THEN 1 WHEN 'Priority' THEN 2 ELSE 3 END,
          id DESC""").fetchall()
    return [request_dict(r) for r in rows]

def get_rooms():
    with connect() as con:
        return [request_dict(r) for r in con.execute("SELECT * FROM rooms ORDER BY id").fetchall()]

def time_to_minutes(value):
    h, m = map(int, value.split(":"))
    return h * 60 + m

def minutes_to_time(value):
    return f"{value // 60:02d}:{value % 60:02d}"

def schedule_pending():
    """Greedy earliest-fit scheduler. Existing appointments remain fixed.
    Every new booking must fit its test-compatible room and staff and cannot overlap
    another booking using that room or staff member.
    """
    with connect() as con:
        rows = con.execute("""SELECT * FROM requests WHERE status='Scheduled'
            ORDER BY requested_date,start_time""").fetchall()
        bookings = [request_dict(r) for r in rows]
        pending = con.execute("""SELECT * FROM requests WHERE status='Pending'
            ORDER BY requested_date,
            CASE priority WHEN 'Urgent (authorised)' THEN 1 WHEN 'Priority' THEN 2 ELSE 3 END,
            created_at,id""").fetchall()
        rooms = [request_dict(r) for r in con.execute("SELECT * FROM rooms WHERE active=1").fetchall()]
        scheduled_count = 0
        for row in pending:
            req = request_dict(row)
            spec = TESTS.get(req["test_type"])
            if not spec:
                continue
            duration = spec["duration"]
            allowed = [r for r in rooms if r["test_type"] == req["test_type"]]
            found = None
            for room in allowed:
                for start in range(START_HOUR * 60, END_HOUR * 60 - duration + 1, SLOT_STEP):
                    end = start + duration
                    conflict = False
                    for b in bookings:
                        if b["requested_date"] != req["requested_date"] or b["status"] != "Scheduled":
                            continue
                        bs, be = time_to_minutes(b["start_time"]), time_to_minutes(b["end_time"])
                        overlap = start < be and end > bs
                        if overlap and (b["room"] == room["name"] or b["staff"] == room["staff"]):
                            conflict = True
                            break
                    # Do not book a slot in the past for today's requests.
                    if req["requested_date"] == date.today().isoformat():
                        now = datetime.now()
                        if start < now.hour * 60 + now.minute + 5:
                            continue
                    if not conflict:
                        found = (room, start, end)
                        break
                if found:
                    break
            if found:
                room, start, end = found
                st, et = minutes_to_time(start), minutes_to_time(end)
                con.execute("""UPDATE requests SET status='Scheduled',room=?,staff=?,start_time=?,end_time=?
                    WHERE id=? AND status='Pending'""", (room["name"], room["staff"], st, et, req["id"]))
                req.update(status="Scheduled", room=room["name"], staff=room["staff"], start_time=st, end_time=et)
                bookings.append(req)
                scheduled_count += 1
        return scheduled_count

@app.get("/")
def home():
    return render_template_string(PAGE)

@app.get("/api/requests")
def api_requests():
    return jsonify(get_requests())

@app.get("/api/rooms")
def api_rooms():
    return jsonify(get_rooms())

@app.get("/api/stats")
def api_stats():
    requests_all = get_requests()
    today = date.today().isoformat()
    scheduled = [r for r in requests_all if r["status"] == "Scheduled"]
    pending = [r for r in requests_all if r["status"] == "Pending"]
    completed = [r for r in requests_all if r["status"] == "Completed"]
    today_bookings = [r for r in scheduled if r["requested_date"] == today]
    # Room utilisation is scheduled minutes / available room minutes, capped at 100%.
    available = len(get_rooms()) * (END_HOUR - START_HOUR) * 60
    used = sum(time_to_minutes(r["end_time"]) - time_to_minutes(r["start_time"])
               for r in today_bookings if r["start_time"] and r["end_time"])
    utilisation = round(100 * used / available) if available else 0
    return jsonify({
        "total": len(requests_all), "pending": len(pending), "scheduled": len(scheduled),
        "completed": len(completed), "utilisation": min(utilisation, 100),
        "today": len(today_bookings), "room_count": len(get_rooms())
    })

@app.post("/api/requests")
def create_request():
    data = request.get_json(silent=True) or {}
    code = str(data.get("patient_code", "")).strip().upper()
    test_type = data.get("test_type")
    priority = data.get("priority")
    requested_date = data.get("requested_date")
    # Require a pseudonymous code, not a name, phone number or medical history.
    if not (3 <= len(code) <= 24) or not all(c.isalnum() or c in "-_" for c in code):
        return jsonify({"error": "Use a pseudonymous ID such as PT-1042 (letters, numbers, hyphens or underscores only)."}), 400
    if test_type not in TESTS:
        return jsonify({"error": "Choose a supported test type."}), 400
    if priority not in PRIORITIES:
        return jsonify({"error": "Choose an authorised scheduling priority."}), 400
    try:
        parsed = date.fromisoformat(requested_date)
        if parsed < date.today():
            return jsonify({"error": "Choose today or a future date."}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a valid date."}), 400
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        cur = con.execute("""INSERT INTO requests
            (patient_code,test_type,priority,requested_date,status,created_at)
            VALUES(?,?,?,?, 'Pending', ?)""", (code, test_type, priority, requested_date, now))
        new_id = cur.lastrowid
        row = con.execute("SELECT * FROM requests WHERE id=?", (new_id,)).fetchone()
    return jsonify(request_dict(row)), 201

@app.post("/api/schedule")
def api_schedule():
    count = schedule_pending()
    return jsonify({"scheduled_now": count, "requests": get_requests()})

@app.post("/api/requests/<int:req_id>/complete")
def complete_request(req_id):
    with connect() as con:
        cur = con.execute("UPDATE requests SET status='Completed' WHERE id=? AND status='Scheduled'", (req_id,))
        if not cur.rowcount:
            return jsonify({"error": "Only scheduled requests can be marked completed."}), 400
    return jsonify({"ok": True})

@app.post("/api/requests/<int:req_id>/cancel")
def cancel_request(req_id):
    with connect() as con:
        cur = con.execute("UPDATE requests SET status='Cancelled' WHERE id=? AND status IN ('Pending','Scheduled')", (req_id,))
        if not cur.rowcount:
            return jsonify({"error": "This request cannot be cancelled."}), 400
    return jsonify({"ok": True})

@app.post("/api/reset")
def reset_demo():
    with connect() as con:
        con.execute("DELETE FROM requests")
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        seed = [
            ("PT-1042", "Blood Test", "Routine", today),
            ("PT-2081", "X-Ray", "Urgent (authorised)", today),
            ("PT-3194", "ECG", "Priority", today),
            ("PT-4270", "Blood Test", "Routine", today),
            ("PT-5516", "ECG", "Routine", tomorrow),
            ("PT-6823", "X-Ray", "Routine", tomorrow),
        ]
        now = datetime.now().isoformat(timespec="seconds")
        con.executemany("""INSERT INTO requests(patient_code,test_type,priority,requested_date,status,created_at)
            VALUES(?,?,?,?,'Pending',?)""", [tuple(x)+(now,) for x in seed])
    return jsonify({"ok": True})

PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MediSlot | Diagnostic Operations</title>
<style>
:root{--ink:#172b4d;--muted:#718096;--line:#e4eaf2;--bg:#f4f7fb;--blue:#315ee8;--blue2:#eaf0ff;--green:#14866d;--amber:#b7791f;--red:#c24150;--card:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit}button{cursor:pointer;border:0;border-radius:10px;padding:10px 14px;font-weight:650;transition:.15s}button:hover{transform:translateY(-1px);filter:brightness(.98)}
.shell{display:grid;grid-template-columns:235px minmax(0,1fr);min-height:100vh}.sidebar{background:#132444;color:#e8eefb;padding:25px 17px;display:flex;flex-direction:column;gap:27px}.brand{display:flex;gap:11px;align-items:center;padding:0 8px}.logo{background:#5478ff;width:39px;height:39px;border-radius:12px;display:grid;place-items:center;font-size:21px}.brand strong{font-size:18px;letter-spacing:-.4px}.brand small{display:block;color:#a9b8d4;font-size:11px;margin-top:1px}.navlabel{color:#91a2c3;text-transform:uppercase;font-size:10px;letter-spacing:1.4px;padding:0 12px}.nav{display:grid;gap:5px}.nav button{background:transparent;color:#c4d0e5;text-align:left;font-weight:550;padding:12px;border-radius:9px}.nav button.active,.nav button:hover{background:#263a60;color:white}.nav button span{display:inline-block;width:27px;font-size:16px}.side-note{margin-top:auto;border:1px solid #344767;border-radius:13px;padding:14px;color:#b9c7df;font-size:12px}.side-note b{color:white;display:block;margin-bottom:5px}.main{min-width:0;padding:27px clamp(17px,3vw,42px) 40px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:27px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);font-weight:750}.topbar h1{font-size:26px;letter-spacing:-.9px;margin:4px 0}.sub{color:var(--muted)}.topright{display:flex;align-items:center;gap:10px}.privacy-chip{border:1px solid #bde8dc;color:#147b65;background:#effbf7;border-radius:999px;padding:8px 11px;font-size:12px;font-weight:750}.primary{background:var(--blue);color:#fff;box-shadow:0 5px 12px #315ee826}.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.danger{color:var(--red);background:#fff0f1}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:20px}.stat,.panel{background:var(--card);border:1px solid var(--line);border-radius:15px;box-shadow:0 3px 12px #213f6810}.stat{padding:17px}.stat-top{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px}.stat-icon{background:#eef2ff;color:var(--blue);width:31px;height:31px;border-radius:9px;display:grid;place-items:center;font-size:16px}.stat-num{font-size:27px;font-weight:780;letter-spacing:-1px;margin:10px 0 1px}.stat-foot{font-size:11px;color:var(--muted)}.content-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(270px,.9fr);gap:18px}.panel{padding:20px;margin-bottom:18px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:17px}.panel h2{font-size:16px;letter-spacing:-.25px;margin:0}.panel p{margin:4px 0 0;color:var(--muted);font-size:12px}.small-action{background:var(--blue2);color:var(--blue);padding:8px 10px;font-size:12px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:590px;text-align:left}th{font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:#8190a7;padding:11px 9px;border-bottom:1px solid var(--line);font-weight:750}td{padding:13px 9px;border-bottom:1px solid #edf1f6;font-size:12px;white-space:nowrap}tr:last-child td{border-bottom:0}.patient{font-weight:750;color:#25385b}.testname{font-weight:650}.muted{color:var(--muted)}.pill{display:inline-flex;padding:4px 8px;border-radius:99px;font-size:10px;font-weight:750}.pending{background:#fff5df;color:#98651b}.scheduled{background:#e8f8f1;color:#147b5e}.completed{background:#eaf0ff;color:#315ee8}.urgent{background:#fff0f1;color:#c13d4d}.priority{background:#fff5df;color:#98651b}.routine{background:#eef2f7;color:#64748b}.actions{display:flex;gap:5px}.actions button{padding:6px 8px;font-size:11px}.room-list{display:grid;gap:11px}.room{border:1px solid var(--line);border-radius:11px;padding:12px}.room-row{display:flex;justify-content:space-between;gap:8px;align-items:center}.room-name{font-weight:750;font-size:12px}.room-type{color:var(--muted);font-size:11px}.room-status{font-size:10px;color:var(--green);background:#effbf7;border-radius:99px;padding:4px 7px}.meter{height:6px;background:#edf1f7;border-radius:99px;margin-top:10px;overflow:hidden}.meter span{height:100%;display:block;background:#5478ff;border-radius:99px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.field{display:grid;gap:6px}.field.full{grid-column:1/-1}.field label{font-size:11px;font-weight:750;color:#596b86}.field input,.field select{width:100%;padding:11px 12px;border:1px solid #dce4ef;border-radius:9px;background:#fff;color:var(--ink);outline:none}.field input:focus,.field select:focus{border-color:#6382f3;box-shadow:0 0 0 3px #315ee81a}.form-note{font-size:11px;color:var(--muted);margin-top:10px}.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:17px}.notice{padding:12px;border-radius:10px;background:#effbf7;border:1px solid #cceee3;color:#226b5b;font-size:12px}.privacy-list{display:grid;gap:10px;margin-top:15px}.privacy-row{display:flex;gap:10px;align-items:flex-start;font-size:12px}.check{background:#e8f8f1;color:#147b5e;border-radius:7px;width:25px;height:25px;display:grid;place-items:center;flex-shrink:0}.privacy-row b{display:block;margin-bottom:2px}.privacy-row span{color:var(--muted)}.privacy-table{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.privacy-cell{background:#f7f9fc;border:1px solid var(--line);border-radius:9px;padding:10px}.privacy-cell b{font-size:11px;display:block}.privacy-cell span{font-size:11px;color:var(--muted)}.privacy-cell.allowed{border-color:#bde8dc;background:#f0fbf7}.privacy-cell.blocked{border-color:#f4d4d7;background:#fff7f7}.privacy-cell.blocked span{color:#b34450}.footer{color:#8b98ad;font-size:11px;text-align:center;padding:15px}.toast{position:fixed;right:22px;bottom:22px;background:#172b4d;color:white;padding:13px 17px;border-radius:11px;box-shadow:0 8px 30px #172b4d33;z-index:5;max-width:360px;display:none}.empty{text-align:center;padding:24px;color:var(--muted)}.hidden{display:none!important}
@media(max-width:1100px){.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.content-grid{grid-template-columns:1fr}.right-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.right-col .panel{margin-bottom:0}}
@media(max-width:760px){.shell{grid-template-columns:1fr}.sidebar{padding:13px 14px;gap:12px}.brand{padding:0}.navlabel,.side-note{display:none}.nav{display:flex;overflow-x:auto}.nav button{white-space:nowrap;padding:9px 11px}.nav button span{width:20px}.main{padding:20px 14px}.topbar{align-items:flex-start;flex-direction:column}.topright{width:100%;justify-content:space-between}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.stat{padding:13px}.stat-num{font-size:23px}.right-col{display:block}.right-col .panel{margin-bottom:18px}.panel{padding:15px}.form-grid{grid-template-columns:1fr}.field.full{grid-column:auto}}
</style>
</head>
<body>
<div class="shell">
<aside class="sidebar">
  <div class="brand"><div class="logo">✚</div><div><strong>MediSlot</strong><small>Diagnostic operations</small></div></div>
  <div class="navlabel">Workspace</div>
  <nav class="nav">
    <button class="active" data-view="overview"><span>▦</span> Overview</button>
    <button data-view="requests"><span>☷</span> Test requests</button>
    <button data-view="resources"><span>▤</span> Rooms & resources</button>
    <button data-view="privacy"><span>♧</span> Privacy centre</button>
  </nav>
  <div class="side-note"><b>Privacy-first by design</b>The scheduler uses pseudonymous IDs and only the operational fields needed to place a test.</div>
</aside>
<main class="main">
  <header class="topbar">
    <div><div class="eyebrow">Hospital operations / Demo environment</div><h1 id="pageTitle">Diagnostic overview</h1><div class="sub" id="todayLabel">Your scheduling command centre</div></div>
    <div class="topright"><span class="privacy-chip">● Privacy controls active</span><button class="primary" onclick="openNewRequest()">＋ New request</button></div>
  </header>
  <section class="stats">
    <div class="stat"><div class="stat-top">Total requests <span class="stat-icon">☷</span></div><div class="stat-num" id="statTotal">—</div><div class="stat-foot">Active diagnostic queue</div></div>
    <div class="stat"><div class="stat-top">Pending <span class="stat-icon">◷</span></div><div class="stat-num" id="statPending">—</div><div class="stat-foot">Awaiting a slot</div></div>
    <div class="stat"><div class="stat-top">Scheduled <span class="stat-icon">✓</span></div><div class="stat-num" id="statScheduled">—</div><div class="stat-foot">Room and time assigned</div></div>
    <div class="stat"><div class="stat-top">Completed <span class="stat-icon">▣</span></div><div class="stat-num" id="statCompleted">—</div><div class="stat-foot">Marked by staff</div></div>
    <div class="stat"><div class="stat-top">Today's room use <span class="stat-icon">↗</span></div><div class="stat-num" id="statUtil">—</div><div class="stat-foot">Scheduled minutes / capacity</div></div>
  </section>
  <section id="overviewView">
    <div class="content-grid">
      <div class="left-col">
        <section class="panel">
          <div class="panel-head"><div><h2>Diagnostic request queue</h2><p>Requests, assigned slots and current status</p></div><button class="small-action" onclick="switchView('requests')">View all ↗</button></div>
          <div class="table-wrap"><table><thead><tr><th>Patient ID</th><th>Test</th><th>Priority</th><th>Appointment</th><th>Status</th><th>Action</th></tr></thead><tbody id="queueBody"></tbody></table></div>
          <div class="empty hidden" id="queueEmpty">No requests yet. Add a request to get started.</div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>Scheduling control</h2><p>Assign compatible rooms and conflict-free time slots</p></div><span class="pill scheduled">Rule-based engine</span></div>
          <div class="notice">The engine checks test compatibility, room and staff overlap, duration, priority, and the operating window (09:00–17:00). Requests that cannot fit remain pending.</div>
          <div style="display:flex;gap:9px;flex-wrap:wrap;margin-top:14px"><button class="primary" onclick="runScheduler()">✦ Auto-schedule pending</button><button class="secondary" onclick="resetDemo()">↻ Reset demo data</button></div>
          <p id="scheduleMessage" style="margin-top:10px"></p>
        </section>
      </div>
      <div class="right-col">
        <section class="panel"><div class="panel-head"><div><h2>Diagnostic rooms</h2><p>Configured room and staff capacity</p></div><button class="small-action" onclick="switchView('resources')">Details</button></div><div class="room-list" id="roomList"></div></section>
        <section class="panel"><div class="panel-head"><div><h2>Privacy by design</h2><p>What the scheduling service can see</p></div><span style="font-size:20px">♧</span></div><div class="privacy-list">
          <div class="privacy-row"><div class="check">✓</div><div><b>Pseudonymous patient ID</b><span>Example: PT-1042, no direct identity fields</span></div></div>
          <div class="privacy-row"><div class="check">✓</div><div><b>Minimum scheduling data</b><span>Test, authorised priority, date and availability</span></div></div>
          <div class="privacy-row"><div class="check">✓</div><div><b>No clinical history in scheduler</b><span>No name, address, phone or diagnosis field</span></div></div>
        </div><button class="secondary" style="width:100%;margin-top:16px" onclick="switchView('privacy')">Explore privacy controls →</button></section>
      </div>
    </div>
  </section>
  <section id="requestsView" class="hidden">
    <section class="panel"><div class="panel-head"><div><h2>All diagnostic requests</h2><p>Filter the queue and manage appointments</p></div><button class="primary" onclick="openNewRequest()">＋ Add request</button></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px"><input id="searchBox" placeholder="Search pseudonymous ID…" style="padding:10px 12px;border:1px solid var(--line);border-radius:9px;min-width:200px" oninput="renderRequests()"><select id="statusFilter" onchange="renderRequests()" style="padding:10px;border:1px solid var(--line);border-radius:9px"><option value="">All statuses</option><option>Pending</option><option>Scheduled</option><option>Completed</option></select><select id="testFilter" onchange="renderRequests()" style="padding:10px;border:1px solid var(--line);border-radius:9px"><option value="">All tests</option><option>Blood Test</option><option>X-Ray</option><option>ECG</option></select></div>
      <div class="table-wrap"><table><thead><tr><th>Patient ID</th><th>Test</th><th>Priority</th><th>Date & time</th><th>Room / staff</th><th>Status</th><th>Action</th></tr></thead><tbody id="allRequestsBody"></tbody></table></div><div class="empty hidden" id="allEmpty">No requests match these filters.</div>
    </section>
  </section>
  <section id="resourcesView" class="hidden">
    <section class="panel"><div class="panel-head"><div><h2>Rooms & resources</h2><p>Compatibility and assigned staff for each diagnostic service</p></div></div><div class="table-wrap"><table><thead><tr><th>Room</th><th>Compatible test</th><th>Assigned team</th><th>Room status</th><th>Operating hours</th></tr></thead><tbody id="resourceBody"></tbody></table></div></section>
    <section class="panel"><h2>Scheduling rules</h2><div class="privacy-list">
      <div class="privacy-row"><div class="check">1</div><div><b>Test compatibility</b><span>Blood tests use lab rooms, X-rays use imaging, and ECGs use ECG rooms.</span></div></div>
      <div class="privacy-row"><div class="check">2</div><div><b>Duration and operating hours</b><span>Blood test 20 min, X-ray 30 min, ECG 15 min. Slots are considered in 15-minute increments between 09:00 and 17:00.</span></div></div>
      <div class="privacy-row"><div class="check">3</div><div><b>No room or staff overlap</b><span>A booking cannot overlap an existing booking in the same room or with the same staff team.</span></div></div>
      <div class="privacy-row"><div class="check">4</div><div><b>Priority ordering</b><span>Authorised urgent requests are considered before priority and routine requests. This is a scheduling demo, not a clinical triage tool.</span></div></div>
    </div></section>
  </section>
  <section id="privacyView" class="hidden">
    <div class="content-grid">
      <section class="panel"><div class="panel-head"><div><h2>Scheduler data boundary</h2><p>Field-level explanation of the prototype's design</p></div><span class="privacy-chip">Minimum necessary</span></div>
        <div class="notice">The request form and scheduler database intentionally do not have fields for names, phone numbers, addresses, or full medical history.</div>
        <div class="privacy-table">
          <div class="privacy-cell allowed"><b>✓ Patient code</b><span>PT-1042</span></div><div class="privacy-cell allowed"><b>✓ Test type</b><span>Blood Test / X-Ray / ECG</span></div>
          <div class="privacy-cell allowed"><b>✓ Authorised priority</b><span>Routine / Priority / Urgent</span></div><div class="privacy-cell allowed"><b>✓ Scheduling constraints</b><span>Date, room, staff, time</span></div>
          <div class="privacy-cell blocked"><b>✕ Full name</b><span>Not collected</span></div><div class="privacy-cell blocked"><b>✕ Address / phone</b><span>Not collected</span></div>
          <div class="privacy-cell blocked"><b>✕ Medical history</b><span>Not collected</span></div><div class="privacy-cell blocked"><b>✕ Diagnosis details</b><span>Not collected</span></div>
        </div>
      </section>
      <section class="panel"><h2>Privacy principles</h2><div class="privacy-list">
        <div class="privacy-row"><div class="check">✓</div><div><b>Data minimisation</b><span>Only scheduling fields are stored.</span></div></div>
        <div class="privacy-row"><div class="check">✓</div><div><b>Pseudonymisation</b><span>Use a code instead of a direct identifier. A code can still be personal data if it can be linked back to someone.</span></div></div>
        <div class="privacy-row"><div class="check">✓</div><div><b>Backend validation</b><span>Request fields are validated by the server, not only by the browser.</span></div></div>
        <div class="privacy-row"><div class="check">✓</div><div><b>Honest prototype boundary</b><span>This local demo has no login or production-grade access-control system. Do not enter real patient information.</span></div></div>
      </div></section>
    </div>
  </section>
  <div class="footer">MediSlot prototype · Fictional demo records only · Not for clinical use</div>
</main>
</div>

<div id="requestModal" class="hidden" style="position:fixed;inset:0;background:#10203b88;z-index:10;display:none;align-items:center;justify-content:center;padding:18px">
  <div class="panel" style="width:min(510px,100%);padding:23px;box-shadow:0 20px 70px #0b1c3833">
    <div class="panel-head"><div><h2>New diagnostic request</h2><p>Only enter a pseudonymous patient ID</p></div><button class="secondary" onclick="closeModal()">✕</button></div>
    <form id="requestForm">
      <div class="form-grid">
        <div class="field full"><label for="patientCode">Pseudonymous patient ID</label><input id="patientCode" required maxlength="24" placeholder="e.g. PT-1042" pattern="[A-Za-z0-9_-]{3,24}"><div class="form-note">Do not enter a name, phone number or medical history.</div></div>
        <div class="field"><label for="testType">Diagnostic test</label><select id="testType" required><option>Blood Test</option><option>X-Ray</option><option>ECG</option></select></div>
        <div class="field"><label for="priority">Authorised priority</label><select id="priority" required><option>Routine</option><option>Priority</option><option>Urgent (authorised)</option></select></div>
        <div class="field full"><label for="requestedDate">Requested date</label><input id="requestedDate" type="date" required></div>
      </div>
      <div id="formError" style="color:#c24150;font-size:12px;margin-top:10px"></div>
      <div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Cancel</button><button class="primary" type="submit">Create request</button></div>
    </form>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
let requests = [], rooms = [], currentView = 'overview', toastTimer;
const todayISO = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
document.getElementById('requestedDate').min = todayISO;
document.getElementById('requestedDate').value = todayISO;
document.getElementById('todayLabel').textContent = new Date().toLocaleDateString(undefined,{weekday:'long',year:'numeric',month:'long',day:'numeric'});
function toast(message){const el=document.getElementById('toast');el.textContent=message;el.style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.style.display='none',3200)}
async function api(url, options={}){const res=await fetch(url,{headers:{'Content-Type':'application/json'},...options});let data={};try{data=await res.json()}catch(e){}if(!res.ok)throw new Error(data.error||'Something went wrong');return data}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function priorityClass(p){return p==='Urgent (authorised)'?'urgent':p==='Priority'?'priority':'routine'}
function statusClass(s){return s==='Scheduled'?'scheduled':s==='Completed'?'completed':'pending'}
function appointment(r){return r.start_time?`${r.requested_date} · ${r.start_time}–${r.end_time}`:r.requested_date}
function actionButtons(r){if(r.status==='Scheduled')return `<div class="actions"><button class="small-action" onclick="completeRequest(${r.id})">Complete</button><button class="secondary" onclick="cancelRequest(${r.id})">Cancel</button></div>`;if(r.status==='Pending')return `<div class="actions"><button class="secondary" onclick="cancelRequest(${r.id})">Cancel</button></div>`;return '<span class="muted">—</span>'}
function rowHtml(r, full=false){return `<tr><td class="patient">${esc(r.patient_code)}</td><td class="testname">${esc(r.test_type)}</td><td><span class="pill ${priorityClass(r.priority)}">${esc(r.priority)}</span></td>${full?`<td>${esc(appointment(r))}</td><td>${r.room?`${esc(r.room)}<br><span class="muted">${esc(r.staff)}</span>`:'<span class="muted">Not assigned</span>'}</td>`:`<td>${r.start_time?esc(r.start_time+'–'+r.end_time):'<span class="muted">Not assigned</span>'}</td>`}<td><span class="pill ${statusClass(r.status)}">${esc(r.status)}</span></td><td>${actionButtons(r)}</td></tr>`}
function renderQueue(){const visible=requests.slice(0,7);document.getElementById('queueBody').innerHTML=visible.map(r=>rowHtml(r)).join('');document.getElementById('queueEmpty').classList.toggle('hidden',visible.length>0)}
function renderRequests(){const q=(document.getElementById('searchBox')?.value||'').toLowerCase();const st=document.getElementById('statusFilter')?.value||'';const tt=document.getElementById('testFilter')?.value||'';const list=requests.filter(r=>(r.patient_code.toLowerCase().includes(q))&&(!st||r.status===st)&&(!tt||r.test_type===tt));document.getElementById('allRequestsBody').innerHTML=list.map(r=>rowHtml(r,true)).join('');document.getElementById('allEmpty').classList.toggle('hidden',list.length>0)}
function renderRooms(){const todayBooked=requests.filter(r=>r.status==='Scheduled'&&r.requested_date===todayISO);document.getElementById('roomList').innerHTML=rooms.map(room=>{const used=todayBooked.filter(r=>r.room===room.name).reduce((sum,r)=>sum+(r.end_time&&r.start_time?toMin(r.end_time)-toMin(r.start_time):0),0);const pct=Math.min(100,Math.round(used/480*100));return `<div class="room"><div class="room-row"><div><div class="room-name">${esc(room.name)}</div><div class="room-type">${esc(room.test_type)} · ${esc(room.staff)}</div></div><span class="room-status">${pct?'In use':'Available'}</span></div><div class="meter"><span style="width:${pct}%"></span></div><div class="room-row" style="margin-top:6px"><span class="muted" style="font-size:10px">Today's scheduled capacity</span><span style="font-size:10px;font-weight:750">${pct}%</span></div></div>`}).join('');document.getElementById('resourceBody').innerHTML=rooms.map(r=>`<tr><td class="patient">${esc(r.name)}</td><td>${esc(r.test_type)}</td><td>${esc(r.staff)}</td><td><span class="pill scheduled">Active</span></td><td>09:00–17:00</td></tr>`).join('')}
function toMin(t){const [h,m]=t.split(':').map(Number);return h*60+m}
async function refresh(){[requests,rooms]=await Promise.all([api('/api/requests'),api('/api/rooms')]);const s=await api('/api/stats');document.getElementById('statTotal').textContent=s.total;document.getElementById('statPending').textContent=s.pending;document.getElementById('statScheduled').textContent=s.scheduled;document.getElementById('statCompleted').textContent=s.completed;document.getElementById('statUtil').textContent=s.utilisation+'%';renderQueue();renderRequests();renderRooms()}
function switchView(view){currentView=view;for(const v of ['overview','requests','resources','privacy'])document.getElementById(v+'View').classList.toggle('hidden',v!==view);document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));document.getElementById('pageTitle').textContent=({overview:'Diagnostic overview',requests:'Test requests',resources:'Rooms & resources',privacy:'Privacy centre'})[view];if(view==='requests')renderRequests();window.scrollTo({top:0,behavior:'smooth'})}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
function openNewRequest(){document.getElementById('formError').textContent='';document.getElementById('requestModal').style.display='flex';document.getElementById('requestModal').classList.remove('hidden');document.getElementById('patientCode').focus()}
function closeModal(){document.getElementById('requestModal').style.display='none';document.getElementById('requestModal').classList.add('hidden')}
document.getElementById('requestForm').addEventListener('submit',async e=>{e.preventDefault();const data={patient_code:document.getElementById('patientCode').value.trim(),test_type:document.getElementById('testType').value,priority:document.getElementById('priority').value,requested_date:document.getElementById('requestedDate').value};try{await api('/api/requests',{method:'POST',body:JSON.stringify(data)});closeModal();document.getElementById('requestForm').reset();document.getElementById('requestedDate').value=todayISO;document.getElementById('requestedDate').min=todayISO;await refresh();toast('Request created. Run Auto-schedule to assign a slot.')}catch(err){document.getElementById('formError').textContent=err.message}});
async function runScheduler(){const btns=document.querySelectorAll('button');try{const d=await api('/api/schedule',{method:'POST',body:'{}'});await refresh();const msg=`${d.scheduled_now} request(s) scheduled. Requests without a compatible free slot remain pending.`;document.getElementById('scheduleMessage').textContent=msg;toast(msg)}catch(e){toast(e.message)}}
async function completeRequest(id){try{await api(`/api/requests/${id}/complete`,{method:'POST',body:'{}'});await refresh();toast('Request marked completed.')}catch(e){toast(e.message)}}
async function cancelRequest(id){if(!confirm('Cancel this diagnostic request?'))return;try{await api(`/api/requests/${id}/cancel`,{method:'POST',body:'{}'});await refresh();toast('Request cancelled.')}catch(e){toast(e.message)}}
async function resetDemo(){if(!confirm('Reset the demo queue to the original fictional sample requests?'))return;try{await api('/api/reset',{method:'POST',body:'{}'});await refresh();document.getElementById('scheduleMessage').textContent='Demo queue reset. You can run the scheduler again.';toast('Demo data reset.')}catch(e){toast(e.message)}}
refresh().catch(e=>toast('Could not load data: '+e.message));
</script>
</body></html>
"""

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
