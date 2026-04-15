# 🚀 CampusBite – Smart Canteen Management System

CampusBite is a **web-based Smart Canteen Management System** designed to modernize and automate traditional canteen operations in educational institutions. It helps users order food online, reduces waiting time, and allows admins to efficiently manage orders and menu items.

---

## 📌 Features

### 👤 User Features
- User Registration & Login  
- Browse Menu (with categories)  
- Add Items to Cart  
- Place Orders  
- Pre-order / Schedule Orders  
- View Order Status  

### 🛠️ Admin Features
- Admin Login  
- Add / Update / Delete Menu Items  
- Manage Orders  
- Update Order Status  
- View Basic Analytics  

---

## 🏗️ Tech Stack

| Layer | Technology |
|------|-----------|
| Frontend | HTML, CSS |
| Backend | Flask (Python) |
| Database | SQLite |
| ORM | SQLAlchemy |
| Forms | Flask-WTF |
| Payment | Razorpay (optional) |

---

## ⚙️ System Architecture

- Client-Server Architecture  
- Frontend handles UI  
- Backend (Flask) processes logic  
- SQLite manages data storage  

---

## 📂 Project Structure

CampusBite/
│
├── app.py  
├── models.py  
├── forms.py  
├── requirements.txt  
├── templates/  
├── static/  
└── database.db  

---

## 🚀 Installation & Setup

1. Clone Repository  
   git clone https://github.com/shrvan28/campusbite.git  

2. Create Virtual Environment  
   python -m venv venv  

3. Activate Environment  
   Windows: venv\Scripts\activate  
   Linux/Mac: source venv/bin/activate  

4. Install Dependencies  
   pip install -r requirements.txt  

5. Run Application  
   python app.py  

Open browser: http://127.0.0.1:5000/

---

## 📊 Workflow

1. User logs in  
2. Browses menu  
3. Places order  
4. Order stored in database  
5. Admin processes order  
6. Status updated  
7. User collects order  

---

## 🎯 Objectives

- Reduce waiting time  
- Automate canteen operations  
- Improve accuracy  
- Enhance user experience  

---

## ⚠️ Limitations

- SQLite not suitable for large-scale systems  
- Limited payment options  
- No real-time notifications  

---

## 📈 Future Enhancements

- Mobile application  
- Online payment integration  
- Real-time notifications  
- Advanced analytics  


## ⭐ Give a star if you like this project!
