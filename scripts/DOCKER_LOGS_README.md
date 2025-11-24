# Docker Log Management

Bu dokümantasyon, Docker container loglarını host'ta saklama ve yönetme sistemini açıklar.

## 📋 Özellikler

- ✅ **Otomatik Log Rotation**: Log dosyaları 500MB'ı aştığında otomatik olarak yeni dosya oluşturulur
- ✅ **Container ID ile Klasörleme**: Her container rebuild'inde farklı klasör oluşturulur (container ID bazlı)
- ✅ **Host'ta Erişilebilir**: Log dosyaları host'ta `~/docker-logs/` altında saklanır
- ✅ **Maksimum 10 Dosya**: Her container için maksimum 10 rotated log dosyası tutulur

## 🏗️ Yapılandırma

### Docker Compose Logging Yapılandırması

Tüm compose dosyalarına (`docker-compose.yml`, `docker-compose.preview.yml`, `docker-compose.staging.yml`) logging yapılandırması eklendi:

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "500m"      # Her log dosyası maksimum 500MB
    max-file: "10"        # Maksimum 10 rotated dosya (toplam ~5GB)
    labels: "container_name=<container-name>"
```

## 📁 Log Dosya Yapısı

Log dosyaları aşağıdaki yapıda saklanır:

```
~/docker-logs/
├── backend-prod/
│   ├── <container-id-1>/
│   │   ├── container-<container-id-1>-current.log
│   │   ├── container-<container-id-1>-20250121_143022.log
│   │   └── container-<container-id-1>-20250121_150045.log
│   └── <container-id-2>/          # Rebuild sonrası yeni container ID
│       └── container-<container-id-2>-current.log
├── frontend-prod/
│   └── <container-id>/
│       └── container-<container-id>-current.log
└── ...
```

## 🔧 Kullanım

### Manuel Log Sync

Log dosyalarını host'a senkronize etmek için:

```bash
# Tek container için
./scripts/docker-log-sync.sh backend-prod

# Birden fazla container için
./scripts/docker-log-sync.sh backend-prod frontend-prod qdrant-service-prod

# Production tüm container'lar için
./scripts/docker-log-sync.sh \
  backend-prod \
  frontend-prod \
  qdrant-service-prod \
  neo4j-service-prod \
  neo4j-service-prod-2
```

### Otomatik Log Sync (Cron Job)

Log dosyalarını otomatik olarak senkronize etmek için cron job ekleyin:

```bash
# Crontab'ı düzenle
crontab -e

# Her 5 dakikada bir sync et
*/5 * * * * /path/to/scripts/docker-log-sync.sh backend-prod frontend-prod >> /tmp/docker-log-sync.log 2>&1

# Veya her saat başı
0 * * * * /path/to/scripts/docker-log-sync.sh backend-prod frontend-prod >> /tmp/docker-log-sync.log 2>&1
```

### Systemd Timer (Alternatif)

Systemd timer kullanarak otomatik sync:

```bash
# /etc/systemd/system/docker-log-sync.service
[Unit]
Description=Docker Log Sync Service
After=docker.service

[Service]
Type=oneshot
ExecStart=/path/to/scripts/docker-log-sync.sh backend-prod frontend-prod
User=your-username

# /etc/systemd/system/docker-log-sync.timer
[Unit]
Description=Docker Log Sync Timer
Requires=docker-log-sync.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target

# Timer'ı aktif et
sudo systemctl enable docker-log-sync.timer
sudo systemctl start docker-log-sync.timer
```

## 🔍 Log Dosyalarını Görüntüleme

### Log Dosyalarını Listeleme

```bash
# Tüm log dosyalarını listele
ls -lh ~/docker-logs/

# Belirli bir container'ın log dosyalarını listele
ls -lh ~/docker-logs/backend-prod/

# Container ID ile log dosyalarını listele
ls -lh ~/docker-logs/backend-prod/<container-id>/
```

### Log Dosyalarını Okuma

```bash
# Son log dosyasını oku
tail -f ~/docker-logs/backend-prod/<container-id>/container-*-current.log

# Tüm log dosyalarını birleştirerek oku
cat ~/docker-logs/backend-prod/<container-id>/*.log

# Belirli bir zaman aralığındaki logları filtrele
grep "2025-01-21" ~/docker-logs/backend-prod/<container-id>/*.log
```

## 🧹 Log Temizleme

### Eski Log Dosyalarını Silme

```bash
# 30 günden eski log dosyalarını sil
find ~/docker-logs -name "*.log" -mtime +30 -delete

# Belirli bir container'ın eski loglarını sil
find ~/docker-logs/backend-prod -name "*.log" -mtime +7 -delete
```

### Log Klasörlerini Temizleme

```bash
# Eski container ID klasörlerini sil (container artık çalışmıyorsa)
# Önce hangi container'ların çalıştığını kontrol et
docker ps --format "{{.ID}}"

# Çalışmayan container'ların log klasörlerini sil
# (Dikkatli kullanın - önce yedek alın)
```

## ⚙️ Yapılandırma

### Log Dizini Değiştirme

Varsayılan log dizini `~/docker-logs`'dur. Değiştirmek için:

```bash
export DOCKER_LOG_DIR=/var/log/docker-containers
./scripts/docker-log-sync.sh backend-prod
```

### Max File Size Değiştirme

Docker Compose dosyalarında `max-size` değerini değiştirin:

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "1g"  # 1GB olarak değiştir
    max-file: "10"
```

## 📊 Log Rotation Detayları

- **Max Size**: 500MB (524288000 bytes)
- **Max Files**: 10 dosya
- **Toplam Kapasite**: ~5GB per container
- **Rotation**: Dosya 500MB'ı aştığında otomatik olarak yeni dosya oluşturulur
- **Eski Dosyalar**: En eski dosyalar otomatik olarak silinir (10 dosya limiti)

## 🔐 İzinler

Log sync script'i Docker log dosyalarına erişmek için:

- **Root erişimi** veya
- **Docker grubuna üyelik** gerektirir

Docker grubuna eklemek için:

```bash
sudo usermod -aG docker $USER
# Sonra logout/login yapın
```

## 🐛 Troubleshooting

### Log Dosyalarına Erişilemiyor

```bash
# Docker log dosyalarının konumunu kontrol et
docker inspect <container-name> | grep LogPath

# İzinleri kontrol et
ls -la /var/lib/docker/containers/<container-id>/

# Docker grubuna üye olduğunuzu kontrol et
groups
```

### Script Çalışmıyor

```bash
# Script'in çalıştırılabilir olduğunu kontrol et
chmod +x scripts/docker-log-sync.sh

# Manuel olarak test et
./scripts/docker-log-sync.sh backend-prod

# Hata mesajlarını kontrol et
./scripts/docker-log-sync.sh backend-prod 2>&1 | tee sync-debug.log
```

## 📝 Notlar

- Log dosyaları Docker'ın varsayılan konumunda (`/var/lib/docker/containers/`) saklanır
- Script bu dosyaları host'ta erişilebilir bir konuma kopyalar
- Her container rebuild'inde yeni bir container ID oluşur ve yeni bir klasör oluşturulur
- Eski container ID'lerin log klasörleri manuel olarak temizlenebilir


