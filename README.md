# Campus360

> A centralized educational management platform for a connected campus.

Campus360 is a web-based educational management system designed to bring
academic activities into one centralized platform.

The frontend currently provides role-based interfaces for:

- Students
- Faculty
- Administrators

The platform covers areas such as attendance, timetables, assignments,
marks and results, announcements, courses, notices, and academic records.

---

## 🚀 Current Status

The **frontend prototype is complete** for the current development phase.

| Area | Status |
|---|---|
| Landing Page | ✅ Complete |
| Login Page | ✅ Complete |
| Student Interface | ✅ Complete |
| Faculty Interface | ✅ Complete |
| Admin Interface | ✅ Complete |
| Role-based Demo Login | ✅ Complete |
| Frontend Navigation | ✅ Complete |
| Responsive UI | ✅ Implemented |
| Backend | ⏳ Planned |
| Database | ⏳ Planned |
| Real Authentication | ⏳ Planned |

### Current Authentication

Authentication is currently **frontend-only demo authentication**.

The login system accepts any non-empty ID and password and routes the user
according to the selected role.

The demo session uses browser `sessionStorage` and is **not real security**.

Real authentication, authorization, password hashing, JWT/session handling,
and persistent user data will be implemented in the backend.

---

## ✨ Features

### Public Landing Page

- Campus360 branding and introduction
- Hero section
- Core platform features
- Role overview
- Platform preview
- How Campus360 works
- Project information
- Contact/CTA section
- Responsive layout

### Authentication

- Student / Faculty / Admin role selection
- Login form validation
- Role-based dashboard routing
- Demo session management
- Protected dashboard pages
- Logout functionality

### Student Portal

Students can access:

- Dashboard
- Attendance
- Timetable
- Assignments
- Marks & Results
- Announcements
- Academic Records

### Faculty Portal

Faculty can access:

- Dashboard
- Attendance
- Assignments
- Marks
- Announcements

### Admin Portal

Administrators can access:

- Dashboard
- Student Management
- Faculty Management
- Courses
- Notices
- Reports

---

## 🛠️ Technology Stack

### Current Frontend

- HTML5
- CSS3
- Vanilla JavaScript

No frontend framework or package manager is currently required.

### Planned Backend

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Real authentication and authorization

The backend will be introduced in the next development phase.

---

## 📁 Project Structure

```text
Campus360/
│
├── index.html
│
├── assets/
│   ├── campus360-orbit.png
│   └── campus360-hero-bg.png
│
├── css/
│   ├── style.css
│   ├── auth.css
│   ├── dashboard.css
│   ├── student.css
│   ├── faculty.css
│   └── admin.css
│
├── js/
│   ├── script.js
│   └── auth.js
│
├── pages/
│   ├── login.html
│   │
│   ├── student/
│   │   ├── dashboard.html
│   │   ├── attendance.html
│   │   ├── timetable.html
│   │   ├── assignments.html
│   │   ├── marks-results.html
│   │   ├── announcements.html
│   │   └── academic-records.html
│   │
│   ├── faculty/
│   │   ├── dashboard.html
│   │   ├── attendance.html
│   │   ├── assignments.html
│   │   ├── marks.html
│   │   └── announcements.html
│   │
│   └── admin/
│       ├── dashboard.html
│       ├── students.html
│       ├── faculty.html
│       ├── courses.html
│       ├── notices.html
│       └── reports.html
│
├── .gitignore
└── README.md