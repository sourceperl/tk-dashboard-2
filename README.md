# tk-dashboard-2


## Setup instructions

### Add some packages to system

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y redis supervisor stunnel4 fail2ban ufw xpdf fonts-freefont-ttf python3-redis
```

### Firewall 

```bash
# UFW firewall setup
sudo ufw allow proto tcp from 192.168.0.0/24 to any port ssh
sudo ufw enable
```

### Redis

```bash
# ...
```

### Global

```bash
sudo mkdir /opt/tk-dashboard
sudo mkdir /opt/tk-dashboard/ui-apps
sudo mkdir /opt/tk-dashboard/io-apps
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
## loos-export-io
# copy
sudo cp -rv  io-apps/conf /opt/tk-dashboard/io-apps/
sudo cp -rv  io-apps/lib /opt/tk-dashboard/io-apps/
sudo cp -rv  io-apps/loos-* /opt/tk-dashboard/ui-apps/
# init python venv
sudo /opt/tk-dashboard/io-apps/loos-venv-setup.sh

## loos-import-io
# copy
sudo cp -rv --dereference io-apps/loos-import-io /opt/tk-dashboard/
# init python venv
sudo /opt/tk-dashboard/loos-import-io/venv_setup.sh

## loos-meters-io
# copy
sudo cp -rv --dereference io-apps/loos-meters-io /opt/tk-dashboard/
# init python venv
sudo /opt/tk-dashboard/loos-meters-io/venv_setup.sh

## for all
echo 'think to populate private_data.py with credentials, URLs...'
echo 'start with cp example_private_data.py private_data.py'
```

### Supervisor

```bash
# supervisor setup
sudo cp supervisor/tk-dashboard-loos.conf /etc/supervisor/conf.d/
sudo supervisorctl update
```