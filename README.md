# MediSlot: Hospital Diagnostic Scheduling Prototype

A beginner-friendly local web app for a hackathon demo. It combines a Flask backend, SQLite persistence, a rule-based scheduler, and a responsive dashboard.

## Features

- Dashboard metrics: active requests, pending, scheduled, completed, and today's room utilisation.
- Create requests for Blood Test, X-Ray, and ECG.
- Pseudonymous patient IDs only. The form and scheduler do not collect names, addresses, phone numbers, diagnoses, or medical histories.
- Automatic earliest-fit scheduling that respects room/test compatibility, test duration, staff conflicts, operating hours, and authorised priority order.
- Pending requests remain pending if no suitable slot is found.
- Complete or cancel requests, filter/search the queue, view rooms, and reset fictional demo data.
- SQLite database saves records between app restarts.

## Requirements

- Python 3.10 or newer
- Internet is only needed once if Flask must be installed from pip.

## Windows setup (beginner steps)

1. Download and extract `medislot_hackathon.zip`.
2. Open the extracted `medislot_hackathon` folder in VS Code.
3. In VS Code, choose **Terminal → New Terminal**.
4. Create a virtual environment:

   ```powershell
   py -m venv .venv
   ```

5. Activate it:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, use Command Prompt in VS Code and run:

   ```bat
   .venv\Scripts\activate.bat
   ```

6. Install Flask:

   ```powershell
   python -m pip install -r requirements.txt
   ```

7. Start the website:

   ```powershell
   python app.py
   ```

8. Open this address in your browser:

   **http://127.0.0.1:5000**

Keep the terminal open while using the website. Press `Ctrl+C` in the terminal to stop the server.

## First demo

1. The site opens with fictional requests.
2. Click **Auto-schedule pending**.
3. Watch the room, time, status, and metrics update.
4. Click **New request** and create another fictional request using an ID such as `PT-7777`.
5. Mark a scheduled request complete, or cancel it.
6. Open **Privacy centre** to explain which fields are and are not collected.
7. Click **Reset demo data** before the next presentation run.

## How the scheduling algorithm works

- Each test type has a configured duration and compatible rooms.
- Requests are considered by requested date, then authorised priority (urgent, priority, routine), then creation time.
- The algorithm checks 15-minute start increments during 09:00–17:00.
- A slot is rejected if the same room or staff member has an overlapping appointment.
- Today's slots in the past (or within five minutes of the current time) are skipped.
- If no compatible slot is found, the request remains pending.
- Existing scheduled bookings are kept fixed when running the scheduler again.

This is a transparent greedy earliest-fit demo algorithm, not a claim of globally optimal scheduling. Priority labels are assumed to have been authorised by staff; this app does not perform clinical triage.

## Files

- `app.py` — Flask server, API, SQLite setup, scheduler, and webpage.
- `requirements.txt` — Python dependency.
- `medislot.db` — created automatically the first time `app.py` runs; contains demo data and later changes.

## Important limitations

This is a hackathon prototype, not a production hospital system. It has no login, role-based access control, audit trail, encryption-at-rest configuration, or integration with hospital systems. Pseudonymous IDs are not guaranteed to be anonymous if someone can link them to an identity. Use fictional demo data only. Do not use it to make clinical decisions or schedule real patients.
