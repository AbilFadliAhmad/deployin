# app.py
from flask import request, render_template, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import bcrypt
import paramiko
from models import app, db, User, Feedback, DeploymentLog, Template
import json
from sqlalchemy import func
from werkzeug.security import generate_password_hash
import subprocess
import socket
import os
import tempfile
import uuid


# --- KONFIGURASI FLASK-LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Jika belum login, lempar ke rute ini


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- RUTE AUTENTIKASI ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password').encode('utf-8')

        user = User.query.filter_by(username=username).first()

        # Pengecekan password menggunakan bcrypt
        if user and bcrypt.checkpw(password, user.password_hash.encode('utf-8')):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Username atau password salah!', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- RUTE LANDING PAGE (PUBLIK) ---
@app.route('/')
def index():
    # Jika user sudah login, langsung lempar ke dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

# --- RUTE DASHBOARD (HARUS LOGIN) ---
# Ubah rute dashboard yang tadinya '/' menjadi '/dashboard'
@app.route('/dashboard')
@login_required
def dashboard():
    templates = Template.query.filter(
        (Template.is_global == True) | (Template.user_id == current_user.id)
    ).all()
    return render_template('dashboard.html', templates=templates)


# --- API UNTUK MENGAMBIL PERINTAH OTOMATIS SAAT DROPDOWN DIPILIH ---
@app.route('/api/get_template/<int:template_id>', methods=['GET'])
@login_required
def get_template(template_id):
    template = Template.query.get(template_id)

    # Pastikan template ada dan user berhak melihatnya
    if template and (template.is_global or template.user_id == current_user.id):
        return jsonify({
            'status': 'success',
            'perintah_default': template.perintah_default
        })

    return jsonify({'status': 'error', 'message': 'Template tidak ditemukan'}), 404

def is_local_target(target_ip: str) -> bool:
    """
    Mengecek apakah IP tujuan adalah server yang sama.
    """

    try:
        target_ip = target_ip.strip()
        local_ips = {
            "127.0.0.1",
            "localhost",
            "::1"
        }
        # Hostname
        try:
            local_ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except Exception:
            pass

        # Semua IP interface Linux
        try:
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                for ip in result.stdout.split():
                    local_ips.add(ip.strip())

        except Exception:
            pass

        return target_ip in local_ips

    except Exception:
        return False

def create_temp_deploy_script(script_content: str):
    """
    Membuat temporary bash script deployment.
    """
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        suffix=".sh",
        newline='\n'  # <--- Tambahkan ini agar aman saat dikirim ke Linux
    )

    temp.write(
        "#!/bin/bash\n"
        "set -e\n"
        "set -o pipefail\n\n"
    )

    temp.write(script_content)
    temp.close()
    os.chmod(temp.name, 0o755)
    return temp.name

def validate_bash_script(script_path):
    """
    Mengecek syntax bash sebelum dijalankan.
    """
    if os.name == "nt":
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr=""
        )

    result = subprocess.run(
        ["bash", "-n", script_path],
        capture_output=True,
        text=True
    )
    return result

def run_local_deploy(script_path, password):
    """
    Menjalankan deployment di server yang sama.
    """
    if os.name == "nt":
        raise RuntimeError(
            "Local Deploy hanya tersedia pada Linux."
        )

    return subprocess.run(
        ["sudo", "-S", "bash", script_path],
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=1800
    )

def run_remote_deploy(
    ip,
    username,
    password,
    script_path
):
    """
    Upload temporary bash script ke server tujuan
    kemudian menjalankannya.
    """
    ssh=None
    sftp=None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(
            port=22,
            hostname=ip,
            username=username,
            password=password,
            timeout=10
        )

        sftp = ssh.open_sftp()

        remote_script = f"/tmp/deploy_{uuid.uuid4().hex}.sh"
        sftp.put(script_path, remote_script)
        sftp.chmod(remote_script, 0o755)
        perintah_remote = f"echo '{password}' | sudo -S bash {remote_script}"
        stdin, stdout, stderr = ssh.exec_command(perintah_remote)

        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()

        ssh.exec_command(f"echo '{password}' | sudo -S rm -f {remote_script}")
        sftp.close()
        ssh.close()

        return exit_status, out, err
    finally:
        if sftp:
            sftp.close()

        if ssh:
            ssh.close()




# --- API UNTUK MENGEKSEKUSI SSH (TAHAP 4 & LOGIKA PORT) ---
@app.route('/api/execute_deploy', methods=['POST'])
@login_required
def execute_deploy():
    data = request.json
    template_id = data.get('template_id')
    ip = data.get('ip')
    password = data.get('password')
    github_link = data.get('github_link')
    perintah_mentah = data.get('perintah')
    port = str(data.get('port','')).strip()  # Pastikan bertipe string dan bersih dari spasi
    kill_port = data.get('kill_port', False)  # Menangkap perintah dari kotak dialog
    env_content = data.get('env', '').strip()
    domain = data.get('domain', '').strip()  # Ambil input domain baru
    username = data.get('username', 'root')

    if not all([ip, password, github_link, perintah_mentah, port]):
        return jsonify({'status': 'error', 'log': 'Semua field utama harus diisi!'}), 400

    # ─── VALIDASI PROTEKSI PORT VITAL VPS (BACKEND PROTECTION) ───
    # Daftar port kritis sistem, panel admin, proxy, dan database utama
    PORT_KRITIS = {
        "22": "SSH (Akses Remote Utama VPS)",
        "80": "HTTP Web Server (Nginx / Apache)",
        "443": "HTTPS Web Server Secure (SSL/TLS)",
        "3306": "MySQL / MariaDB Database",
        "5432": "PostgreSQL Database",
        "6379": "Redis Cache System",
        "27017": "MongoDB Database",
        "9000": "PHP-FPM / Port Utama Management Service",
        "9050": "Tor / Proxy Core Service",
        "8888": "Web Panel Admin (aaPanel / CyberPanel)",
        "2083": "cPanel Web Panel",
        "2087": "WHM Admin Panel"
    }

    if port in PORT_KRITIS:
        pesan_blokir = (
            f"[DEPLOYIN SECURITY] ❌ Eksekusi dibatalkan! "
            f"Port {port} dideteksi sebagai port vital VPS untuk layanan: {PORT_KRITIS[port]}. "
            f"Port ini dilarang keras untuk dimatikan (kill) demi menjaga stabilitas server."
        )
        return jsonify({'status': 'error', 'log': pesan_blokir}), 403
    # ─────────────────────────────────────────────────────────────

    # 1. LOGIKA PEMBENTUKAN TARGET DIR
    # Mengambil username dan nama repo dari link GitHub
    # Contoh: https://github.com/petani/aplikasiku.git -> petani_aplikasiku
    try:
        link_bersih = github_link.replace('.git', '').rstrip('/')
        parts = link_bersih.split('/')
        if len(parts) >= 2:
            target_dir = f"{parts[-2]}_{parts[-1]}"
        else:
            target_dir = "app_deployment_default"
    except Exception:
        target_dir = "app_deployment_default"

    # 2. LOGIKA PENGECEKAN & PENGHANCURAN PORT
    perintah_awal = ""
    if port and kill_port:
        # fuser -k akan mematikan proses (kill) yang memakai port tersebut
        # "|| true" digunakan agar script tidak error jika port ternyata kosong
        perintah_awal = f"fuser -k {port}/tcp || true ; "

    # 3. KUSTOMISASI PEMBUATAN FILE .ENV OTOMATIS
    perintah_env = ""
    if env_content:
        # Menggunakan ./.env agar file dibuat di dalam folder apa pun yang sedang aktif (CD) saat itu
        env_aman = env_content.replace("'", "'\\''")
        perintah_env = f"\necho '{env_aman}' > ./.env\n"

    # 4. LOGIKA BARU: OTOMASI NGINX REVERSE PROXY
    if domain:
        port_bind = f"127.0.0.1:{port}"

        perintah_nginx = f"""
mkdir -p /etc/nginx/sites-available
mkdir -p /etc/nginx/sites-enabled

cat <<'EOF' > /etc/nginx/sites-available/{target_dir}
server {{
    listen 80;
    server_name {domain};

    location / {{
        proxy_pass http://127.0.0.1:{port};

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
EOF

ln -sfn "/etc/nginx/sites-available/{target_dir}" "/etc/nginx/sites-enabled/{target_dir}"
rm -f /etc/nginx/sites-enabled/default || true
nginx -t
systemctl reload nginx
        """
        print(perintah_nginx)
        return jsonify({'status': 'error', 'log': perintah_awal}), 403
    else:
        # Jika domain KOSONG, gunicorn langsung dibuka ke publik, Nginx dikosongkan (string kosong)
        port_bind = f"0.0.0.0:{port}"
        perintah_nginx = "true"

    # 5. MENGGANTI VARIABEL DI TEMPLATE PERINTAH
    perintah_siap_eksekusi = perintah_mentah.replace('{github_link}', github_link)
    perintah_siap_eksekusi = perintah_siap_eksekusi.replace('{target_dir}', target_dir)
    perintah_siap_eksekusi = perintah_siap_eksekusi.replace('{port}', str(port))
    perintah_siap_eksekusi = perintah_siap_eksekusi.replace('{env}', perintah_env)
    perintah_siap_eksekusi = perintah_siap_eksekusi.replace('{port_bind}', port_bind)
    perintah_siap_eksekusi = perintah_siap_eksekusi.replace('{nginx_configuration}', perintah_nginx)

    # Gabungkan perintah matikan port dengan perintah template
    perintah_final = perintah_awal + perintah_siap_eksekusi

    script_path = create_temp_deploy_script(perintah_final)

    check = validate_bash_script(script_path)
    print('sebelumCHeck: ', check)

    if check.returncode != 0:
        os.remove(script_path)

        return jsonify({
            "status": "error",
            "log": check.stderr
        })
    print('sesudahCheck')

    try:

        status_deploy = "success"

        if is_local_target(ip):
            deploy_mode = "LOCAL"

            result = run_local_deploy(
                script_path,
                password
            )

            exit_status = result.returncode
            out = result.stdout
            err = result.stderr
        else:
            print('remote: ', script_path)
            deploy_mode = "REMOTE"
            exit_status, out, err = run_remote_deploy(
                ip,
                username,
                password,
                script_path
            )

        full_log = (
            f"--- DEPLOY MODE ---\n"
            f"{deploy_mode}\n\n"
            f"--- VARIABEL OTOMATIS ---\n"
            f"Target Dir : {target_dir}\n"
            f"Port       : {port}\n\n"
            f"--- OUTPUT ---\n"
            f"{out}\n"
        )



        if err.strip() and exit_status != 0:
            full_log += (
                "\n--- ERROR / WARNING ---\n"
                f"{err}"
            )

        status_deploy = "success" if exit_status == 0 else "fail"
        return jsonify({
            "status": "success" if exit_status == 0 else "warning",
            "log": full_log
        })



    except paramiko.AuthenticationException:
        status_deploy = "fail"
        return jsonify({
            "status": "error",
            "log": "Autentikasi SSH gagal."
        })


    except Exception as e:
        status_deploy = "fail"
        return jsonify({
            "status": "error",
            "log": f"Terjadi kesalahan: {str(e)}"
        })



    finally:

        # try:
        #
        #     if script_path and os.path.exists(script_path):
        #         os.remove(script_path)
        #
        # except Exception:
        #
        #     pass

        log = DeploymentLog(

            user_id=current_user.id,

            status=status_deploy,

            github_link=github_link,

            app=target_dir,

            template_id=template_id

        )

        db.session.add(log)
        db.session.commit()

# --- RUTE MANAJEMEN TEMPLATE ---
@app.route('/manage-templates', methods=['GET', 'POST'])
@login_required
def manage_templates():
    if request.method == 'POST':
        nama_teknologi = request.form.get('nama_teknologi')
        perintah_default = request.form.get('perintah_default')

        # Cek apakah ini dicentang sebagai template global
        # (Hanya berlaku jika yang menekan tombol adalah admin)
        is_global = False
        if current_user.role == 'admin' and request.form.get('is_global') == 'on':
            is_global = True

        if nama_teknologi and perintah_default:
            new_template = Template(
                nama_teknologi=nama_teknologi,
                perintah_default=perintah_default,
                is_global=is_global,
                # Jika global, user_id dikosongkan. Jika pribadi, isi dengan ID pembuatnya.
                user_id=None if is_global else current_user.id
            )
            db.session.add(new_template)
            db.session.commit()
            flash('Template baru berhasil ditambahkan!', 'success')
        else:
            flash('Gagal! Nama teknologi dan perintah tidak boleh kosong.', 'error')

        return redirect(url_for('manage_templates'))

    # Menampilkan daftar template di tabel
    # Jika Admin: Bisa melihat SEMUA template di database
    # Jika User biasa: Hanya bisa melihat template pribadi miliknya
    if current_user.role == 'admin':
        templates = Template.query.all()
    else:
        templates = Template.query.filter_by(user_id=current_user.id).all()

    return render_template('manage_template.html', templates=templates)


# --- RUTE UNTUK MENGEDIT TEMPLATE ---
@app.route('/edit-template/<int:id>', methods=['POST'])
@login_required
def edit_template(id):
    template = Template.query.get_or_404(id)

    # Validasi Keamanan: Hanya Admin atau Pembuat Template yang boleh mengedit
    if current_user.role == 'admin' or template.user_id == current_user.id:
        template.nama_teknologi = request.form.get('nama_teknologi')
        template.perintah_default = request.form.get('perintah_default')

        # Cek checkbox global (Hanya Admin)
        if current_user.role == 'admin':
            template.is_global = True if request.form.get('is_global') == 'on' else False

        db.session.commit()
        flash('Template berhasil diperbarui!', 'success')
    else:
        flash('Akses ditolak! Anda tidak diizinkan mengedit template ini.', 'error')

    return redirect(url_for('manage_templates'))


# --- RUTE UNTUK MENGHAPUS TEMPLATE ---
@app.route('/delete-template/<int:id>', methods=['POST'])
@login_required
def delete_template(id):
    template = Template.query.get_or_404(id)

    # Validasi Keamanan: Admin bisa hapus apapun, User hanya bisa hapus miliknya sendiri
    if current_user.role == 'admin' or template.user_id == current_user.id:
        db.session.delete(template)
        db.session.commit()
        flash('Template berhasil dihapus!', 'success')
    else:
        flash('Akses ditolak! Anda tidak bisa menghapus template ini.', 'error')

    return redirect(url_for('manage_templates'))


# --- RUTE MANAJEMEN USER (KHUSUS ADMIN) ---
@app.route('/manage-users', methods=['GET', 'POST'])
@login_required
def manage_users():
    # Keamanan Ganda: Cek apakah yang akses benar-benar admin
    if current_user.role != 'admin':
        flash('Akses Ditolak! Halaman ini hanya untuk Administrator.', 'error')
        return redirect(url_for('dashboard'))

    # Jika Admin mengirim form (Tambah User Baru)
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        new_role = request.form.get('role', 'user')  # Default 'user'

        # Cek apakah username sudah ada di database
        user_exists = User.query.filter_by(username=new_username).first()

        if user_exists:
            flash(f'Gagal! Username "{new_username}" sudah digunakan.', 'error')
        elif not new_username or not new_password:
            flash('Gagal! Username dan password harus diisi.', 'error')
        else:
            # Enkripsi sandi sebelum disimpan
            hashed_pw = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            new_user = User(username=new_username, password_hash=hashed_pw, role=new_role)
            db.session.add(new_user)
            db.session.commit()
            flash(f'Berhasil! Akun baru "{new_username}" telah ditambahkan.', 'success')

        return redirect(url_for('manage_users'))

    # Mengambil semua daftar user dari database (kecuali dirinya sendiri opsional, tapi di sini kita tampilkan semua)
    users = User.query.all()
    return render_template('manage_users.html', users=users)


# --- RUTE HAPUS USER ---
@app.route('/delete-user/<int:id>', methods=['POST'])
@login_required
def delete_user(id):
    if current_user.role != 'admin':
        flash('Akses Ditolak!', 'error')
        return redirect(url_for('dashboard'))

    user_to_delete = User.query.get_or_404(id)

    # Proteksi: Admin tidak boleh menghapus akunnya sendiri
    if user_to_delete.id == current_user.id:
        flash('Anda tidak dapat menghapus akun Anda sendiri!', 'error')
    else:
        # Menghapus user
        # (Secara otomatis akan menghapus template miliknya jika di model diatur cascade,
        # tapi jika error karena foreign key, kita manual hapus templatenya dulu)
        Template.query.filter_by(user_id=user_to_delete.id).delete()

        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'Akun "{user_to_delete.username}" berhasil dihapus.', 'success')

    return redirect(url_for('manage_users'))

@app.route('/kirim-feedback', methods=['GET', 'POST'])
@login_required
def kirim_feedback():
    if request.method == 'POST':
        jenis = request.form.get('jenis')
        pesan = request.form.get('pesan')

        if not jenis or not pesan:
            flash('Gagal! Semua kolom wajib diisi.', 'error')
            return redirect(url_for('kirim_feedback'))

        # Simpan ke database
        baru_feedback = Feedback(user_id=current_user.id, jenis=jenis, pesan=pesan)
        db.session.add(baru_feedback)
        db.session.commit()

        flash('Terima kasih! Laporan/saran Anda berhasil dikirim ke Admin.', 'success')
        return redirect(url_for('dashboard')) # Alihkan kembali ke dashboard setelah sukses

    return render_template('kirim_feedback.html')


# --- RUTE ANALISA & FEEDBACK (KHUSUS ADMIN) ---
@app.route('/analisa-aplikasi')
@login_required
def analisa_aplikasi():
    if current_user.role != 'admin':
        flash('Akses Ditolak! Halaman ini hanya untuk Administrator.', 'error')
        return redirect(url_for('dashboard'))

    # 1. Ambil Data Masukan (Feedback) urut dari yang paling baru
    semua_feedback = Feedback.query.order_by(Feedback.tanggal.desc()).all()

    # 2. Agregasi Data Grafik: Rasio Sukses vs Gagal
    status_counts = db.session.query(DeploymentLog.status, func.count(DeploymentLog.id)) \
        .group_by(DeploymentLog.status).all()

    chart_status_labels = [row[0].upper() for row in status_counts]
    chart_status_data = [row[1] for row in status_counts]

    # 3. Agregasi Data Grafik: Ambil Top 5 User Teraktif
    top_5_users = db.session.query(User.username, func.count(DeploymentLog.id)) \
        .join(DeploymentLog) \
        .group_by(User.id) \
        .order_by(func.count(DeploymentLog.id).desc()) \
        .limit(5).all()

    # Format data untuk Chart.js grafik batang
    chart_user_labels = [row[0] for row in top_5_users]
    chart_user_data = [row[1] for row in top_5_users]

    # Format data untuk list HTML Top 5 (dikirim dalam bentuk list dari dict)
    top_users_list = [{"username": row[0], "total": row[1]} for row in top_5_users]

    # === DATA BARU: LOG DEPLOYMENT GLOBAL UNTUK TABEL & MODAL ===
    logs_mentah = DeploymentLog.query.order_by(DeploymentLog.tanggal.desc()).all()

    logs_list = []
    for log in logs_mentah:
        logs_list.append({
            "id": log.id,
            "username": log.user.username if log.user else "Unknown",
            "app": log.app,
            "github": log.github_link or "-",
            "status": log.status.upper(),
            "teknologi": log.template.nama_teknologi if log.template else "Custom Template",
            "tanggal": log.tanggal.strftime('%d %b %Y - %H:%M')
        })

    return render_template('analisa_aplikasi.html',
                           feedbacks=semua_feedback,
                           chart_status_labels=json.dumps(chart_status_labels),
                           chart_status_data=json.dumps(chart_status_data),
                           chart_user_labels=json.dumps(chart_user_labels),
                           chart_user_data=json.dumps(chart_user_data),
                           top_users=top_users_list,
                           deployment_logs_json=json.dumps(logs_list))  # Variabel JSON baru


# --- RUTE UPDATE DATA PENGGUNA (KHUSUS ADMIN) ---
@app.route('/update-user/<int:user_id>', methods=['POST'])
@login_required
def update_user(user_id):
    # Proteksi ganda: Pastikan hanya admin yang bisa mengeksekusi
    if current_user.role != 'admin':
        flash('Akses Ditolak! Anda tidak memiliki izin untuk mengubah data.', 'error')
        return redirect(url_for('dashboard'))

    # Ambil data user dari database berdasarkan ID
    user = User.query.get_or_404(user_id)

    # Mencegah admin mengedit akunnya sendiri melalui rute ini (demi keamanan sesi)
    if user.id == current_user.id:
        flash('Untuk keamanan, ubah profil Anda melalui menu pengaturan akun.', 'error')
        return redirect(url_for('manage_users'))  # Sesuaikan dengan nama fungsi rute manajemen Anda

    # Ambil data dari form input modal
    username_baru = request.form.get('username').strip()
    role_baru = request.form.get('role')
    password_baru = request.form.get('password')

    # Validasi: Cek apakah username baru kembar dengan user lain di database
    username_exist = User.query.filter(User.username == username_baru, User.id != user_id).first()
    if username_exist:
        flash(f'Gagal! Username "{username_baru}" sudah digunakan oleh orang lain.', 'error')
        return redirect(request.referrer or url_for('dashboard'))

    try:
        # 1. Update data dasar
        user.username = username_baru
        user.role = role_baru

        # 2. Update password HANYA JIKA kolom password diisi oleh admin
        if password_baru and password_baru.strip() != '':
            user.password_hash = generate_password_hash(password_baru)
            flash(f'Data akun {user.username} dan password berhasil diperbarui!', 'success')
        else:
            flash(f'Data akun {user.username} berhasil diperbarui (Tanpa ganti password).', 'success')

        # Simpan perubahan ke database SQLite
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        flash(f'Terjadi kesalahan sistem saat memperbarui data: {str(e)}', 'error')

    return redirect(request.referrer or url_for('dashboard'))

def init_database():
    # 1. Buka konteks aplikasi agar bisa berinteraksi dengan database
    with app.app_context():
        # 2. Buat file SQLite dan tabel-tabelnya jika belum ada
        db.create_all()

        # 3. Cek apakah tabel User masih kosong
        if not User.query.first():
            print("Database kosong terdeteksi. Memulai proses seeding...")

            # 1. Buat password hash (Sandi: 123)
            hashed_pw = bcrypt.hashpw(b'123', bcrypt.gensalt()).decode('utf-8')

            # 2. Buat User Admin
            admin = User(username='admin', password_hash=hashed_pw, role='admin')
            db.session.add(admin)

            # 3. Daftar nama user yang ingin ditambahkan
            daftar_nama = [
                'abdul', 'wildan', 'galang', 'fadli', 'mukaim', 'fachrul',
                'satrio', 'huda', 'alana', 'yafi', 'alfan', 'bayu',
                'riswan', 'ilham', 'fardhan', 'ridho', 'leo', 'abrar'
            ]

            # 4. Looping untuk membuat object User secara massal
            for nama in daftar_nama:
                baru = User(username=nama, password_hash=hashed_pw, role='user')
                db.session.add(baru)

            # 5. Buat Template Global
            template = Template(
                nama_teknologi='Python Flask (Gunicorn)',
                perintah_default='pkill -f "gunicorn.*:{port}" || true && mkdir -p /deployin && cd /deployin && mkdir -p flask && cd flask && rm -rf {target_dir} && git clone {github_link} {target_dir} && cd {target_dir} && {env} sudo apt install python3-pip python3-venv nginx -y && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt && pip install gunicorn && rm -rf gunicorn.conf.py && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw allow {port} && sudo ufw allow 22 && gunicorn --bind {port_bind} app:app --daemon && {nginx_configuration}',
                is_global=True
            )
            db.session.add(template)

            # 6. Simpan semua data sekaligus ke database
            db.session.commit()
            print(f"Data awal berhasil dibuat! Admin dan {len(daftar_nama)} user telah ditambahkan.")
        else:
            print("Database sudah berisi data. Melewati proses seeding.")

init_database()


# --- JALANKAN APLIKASI & AUTO SETUP DATABASE ---
if __name__ == '__main__':

    # 4. Jalankan server Flask
    app.run(debug=True, port=5000)
