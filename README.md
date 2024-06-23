# tk-dashboard-2


## Setup instructions

### Add some packages to system

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y redis supervisor stunnel4 fail2ban ufw xpdf fonts-freefont-ttf fonts-noto-core
sudo apt install -y python3-redis python3-pil python3-pil.imagetk
```

### Firewall

```bash
# UFW firewall setup
sudo ufw allow proto tcp from 192.168.0.0/24 to any port ssh
sudo ufw enable
```

### Global

```bash
sudo mkdir -p /opt/tk-dashboard/ui-apps
sudo mkdir -p /opt/tk-dashboard/io-apps
sudo mkdir -p /opt/tk-dashboard/virtualenvs
```

### Loos UI (user interface)

```bash
# copy
sudo cp -rv  ui-apps/conf /opt/tk-dashboard/ui-apps/
sudo cp -rv  ui-apps/lib /opt/tk-dashboard/ui-apps/
sudo cp -rv  ui-apps/loos-ui-app.py /opt/tk-dashboard/ui-apps/
```

### Loos IO (input/output)

```bash
# init python venv
sudo cp -rv virtualenvs/loos /opt/tk-dashboard/virtualenvs/
sudo /opt/tk-dashboard/virtualenvs/loos/loos-venv-setup.sh

# copy
sudo cp -rv io-apps/conf /opt/tk-dashboard/io-apps/
sudo cp -rv io-apps/lib /opt/tk-dashboard/io-apps/
sudo cp -rv io-apps/loos-* /opt/tk-dashboard/io-apps/

## for all
echo 'think to populate private_data.py with credentials, URLs...'
echo 'start with cp example_private_data.py private_data.py'
```

```bash
# supervisor setup
sudo cp supervisor/tk-dashboard-loos.conf /etc/supervisor/conf.d/
sudo supervisorctl update
```

### Mag UI (user interface)

```bash
# copy
sudo mkdir -pv /opt/tk-dashboard/ui-apps/conf
sudo cp -v ui-apps/conf/example_private_mag.py /opt/tk-dashboard/ui-apps/conf/
sudo cp -rv ui-apps/lib /opt/tk-dashboard/ui-apps/
sudo cp -rv ui-apps/mag-* /opt/tk-dashboard/ui-apps/
```

### Mag IO (input/output)

```bash
# init python venv
sudo cp -rv virtualenvs/mag /opt/tk-dashboard/virtualenvs/
sudo /opt/tk-dashboard/virtualenvs/mag/mag-venv-setup.sh

# copy
sudo mkdir -pv /opt/tk-dashboard/io-apps/conf
sudo cp -v io-apps/conf/example_private_mag.py /opt/tk-dashboard/io-apps/conf/
sudo cp -rv io-apps/lib /opt/tk-dashboard/io-apps/
sudo cp -v io-apps/mag-* /opt/tk-dashboard/io-apps/

## for all
echo 'think to populate private_data.py with credentials, URLs...'
echo 'start with cp example_private_data.py private_data.py'
```

```bash
# supervisor setup
sudo cp supervisor/tk-dashboard-mag.conf /etc/supervisor/conf.d/
sudo supervisorctl update
```

### Stunnel

```bash
# ...
```

### Redis

```bash
# ...
```


## HOWTOs

### SSL/TLS

```bash
# create private key and self-signed certificate for server
target_prefix=loos-redis-cli
sudo openssl req -x509 -newkey rsa:4096 -days 3650 -nodes \
                 -subj "/C=FR/ST=Haut-de-France/L=Loos/CN=dashboard-loos-master-srv" \
                 -keyout ${target_prefix}.key \
                 -out ${target_prefix}.crt
```
