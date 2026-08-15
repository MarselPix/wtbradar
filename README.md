# 📡 WTB Radar / Notifier Bot 🤖

Bot Telegram monitor WTB (Want To Buy) real-time yang super ringan, hemat RAM, dan dioptimalkan khusus untuk berjalan di **Termux (Android)** maupun VPS/PC.

---

## 🌟 Fitur Utama

- ⚡ **Real-Time Monitoring**: Memantau banyak channel Telegram publik maupun private secara bersamaan menggunakan Pyrogram MTProto.
- 🎯 **Smart Keyword Matching**: Mendukung kata kunci pencarian (WTB) & kata kunci pengecualian (WTS/Jual) secara presisi.
- 🔥 **Hot-Reload Config**: Edit `keywords.txt` atau `exclude.txt` kapan saja tanpa perlu merestart bot.
- 📱 **Interactive Control Panel**: Tambah/hapus channel, keyword, dan cek status bot langsung lewat percakapan chat Bot Telegram.
- 🔗 **Direct Jump Button**: Notifikasi dilengkapi tombol inline yang langsung mengarahkan ke postingan/komentar asli.
- 🛡️ **Anti-Spam & Low RAM**: Fitur cooldown anti-duplikat, tanpa database berat, konsumsi RAM hanya ~30-60 MB.

---

## 🛠️ Panduan Instalasi di Termux (Android)

### 1. Install Package Dasar di Termux
Buka aplikasi Termux, lalu jalankan perintah berikut:

```bash
pkg update && pkg upgrade -y
pkg install python clang make libjpeg-turbo -y
```

### 2. Clone Project & Install Dependencies
Pindah ke folder project:

```bash
git clone https://github.com/USERNAME_LO/wtbradar.git
cd wtbradar
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Buat File Konfigurasi
Buat file `config.json` dari template `config.example.json`:

```bash
cp config.example.json config.json
```

Edit `config.json` menggunakan editor (misal: `nano config.json`) dan isi kredensial Anda:

```json
{
  "api_id": 35140584,
  "api_hash": "b222b0f37b669d2422ce9fc4f9f6b99c",
  "bot_token": "8088797533:AAFo6XNcQzCEDv8gsD735THufH4Eazvy75I",
  "target_chat_id": 6406479004,
  "channels": [],
  "cooldown_seconds": 30,
  "max_message_preview": 300
}
```

---

## 🚀 Cara Menjalankan Bot

### Jalankan Pertama Kali (Login Session)
Jalankan bot secara langsung untuk login akun Telegram Anda pertama kali:

```bash
python main.py
```

- Pyrogram akan meminta nomor telepon (format: `+628xxx`).
- Masukkan kode verifikasi Telegram (dan Password 2FA jika ada).
- Session akan tersimpan secara lokal di file `wtb_radar_session.session`.

### Menjalankan di Background (Agar tetap jalan saat Termux ditutup)

Gunakan `tmux` atau `nohup`:

**Menggunakan `tmux` (Direkomendasikan):**
```bash
pkg install tmux -y
tmux new -s radar
python main.py
```
*(Tekan `Ctrl+B` lalu `D` untuk detach dari tmux session).*

Untuk membuka kembali session tmux:
```bash
tmux attach -t radar
```

---

## 🎮 Perintah Kontrol via Bot Telegram

Anda bisa mengontrol bot langsung dengan mengirim pesan ke Bot Telegram Anda:

| Perintah | Deskripsi |
|---|---|
| `/help` | Menampilkan menu bantuan & daftar perintah |
| `/status` | Cek status bot, jumlah channel & keyword aktif |
| `/channels` | Lihat daftar channel yang sedang dimonitor |
| `/addchannel <@username/link>` | Tambah channel (publik @username, link private, atau ID) |
| `/delchannel <@username>` | Hapus channel dari daftar monitor |
| `/keywords` | Lihat kata kunci WTB aktif |
| `/addkw <kata>` | Tambah kata kunci WTB baru |
| `/delkw <kata>` | Hapus kata kunci WTB |
| `/excludes` | Lihat kata kunci pengecualian (WTS, dll) |
| `/addex <kata>` | Tambah kata kunci pengecualian |
| `/delex <kata>` | Hapus kata kunci pengecualian |

---

## 📂 Struktur File Project

```
wtbradar/
├── main.py              # Entry point utama
├── config.py            # Pengelola konfigurasi & hot-reload
├── handler.py           # Engine filter kata & notifikasi
├── notifier.py          # Modul pengirim notifikasi Bot API
├── channel_manager.py   # Parser & resolver channel publik/private
├── bot_runner.py        # Controller perintah bot interaktif
├── cooldown.py          # Anti-duplicate TTL cache
├── config.json          # Credentials & daftar channel
├── keywords.txt         # Daftar kata kunci WTB
├── exclude.txt          # Daftar kata kunci yang diabaikan
├── requirements.txt     # Daftar dependency Python
└── README.md            # Panduan instalasi
```
