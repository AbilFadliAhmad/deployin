# Deployin 🚀

**Deployin** is a powerful, web-based Virtual Private Server (VPS) deployment orchestrator. It bridges the gap between manual CLI server management and modern UI convenience, allowing developers to automate application deployments, manage Nginx reverse proxies, and handle SSH executions directly from a browser.

---

## 📖 About The Project

Deploying applications manually via SSH can be prone to human error, port conflicts, and tedious Nginx configurations. **Deployin** solves this by providing a centralized dashboard to automate Git cloning, dependency installations, port assignments, and reverse proxy routing without requiring direct terminal access. 

Built with **Python (Flask)** and **Paramiko**, Deployin acts as a secure middleman between the user and the Linux VPS, ensuring deployments are executed safely and efficiently.

### 🌟 Key Features

* **🔒 Smart Port Protection:** A built-in security algorithm that prevents users from accidentally killing critical system ports (e.g., 22 for SSH, 80/443 for Nginx, 3306 for MySQL).
* **🌐 Automated Nginx Orchestration:** Dynamically creates, links, and safely restarts Nginx reverse proxy configurations (`sites-available` to `sites-enabled`) without causing symlink loops or breaking existing server deployments.
* **⚡ Live Terminal UI:** Features a real-time simulated console in the frontend that streams `stdout` and `stderr` directly from the VPS, providing instant feedback.
* **⚙️ Centralized Environment Injector:** Securely injects custom `.env` variables directly into the target application's directory during deployment.
* **📊 Comprehensive Activity Logging:** Tracks all deployment metrics, statuses (Success/Warning/Failed), and GitHub repositories tied to individual user accounts.

---

## 🛠️ Built With

* **Backend:** Python 3, Flask, SQLAlchemy, Paramiko (SSH Protocol)
* **Frontend:** HTML5, Tailwind CSS, Vanilla JavaScript
* **Database:** SQLite / PostgreSQL
* **Target Server Compatibility:** Ubuntu/Debian (with Nginx)

---

## 🚀 Getting Started

To get a local copy up and running, follow these simple steps.

### Prerequisites
* Python 3.8+
* pip (Python package manager)
* A target VPS running Ubuntu/Debian with SSH access enabled.

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/AbilFadliAhmad/deployin.git](https://github.com/AbilFadliAhmad/deployin.git)
   cd deployin
   
2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate

3. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   
4. **Set up the database:**
   ```bash
   flask db upgrade
   # or run your custom initialization script

5. **Run the application:**
   ```bash
      flask run --host=0.0.0.0 --port=5000
__Access the dashboard via http://localhost:5000__

## 💡 Usage
1. **Log In:** Access the Deployin dashboard using your user or admin credentials.

2. **Select Template:** Choose a deployment template (e.g., Python Flask, Node.js).

3. **Configure Payload:** 
   * Input the target VPS IP and Password.
   * Paste the GitHub repository link.
   * Define the specific Port (e.g., 4818) and custom Domain (e.g., app.yourdomain.com).

4. **Deploy:**** Click "Deploy" and watch the live terminal stream the SSH execution process. Deployin will automatically handle port clearing, Nginx routing, and service restarts.

## 🔒 Security Notice
Deployin uses the Paramiko library to establish SSH connections. It is highly recommended to host Deployin on a secured, private network or behind a strict firewall, and always use HTTPS in production to protect VPS credentials transmitted during deployment.

## 📝 License
Distributed under the **MIT License**. See LICENSE for more information.

## 👨‍💻 Author
Abil Fadli Ahmad Computer Science Student | Software Engineer GitHub: @AbilFadliAhmad
