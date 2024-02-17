# tk-dashboard-2


## Setup instructions

### Loos UI (user interface)

```bash
# copy
cp -rv --dereference ./ui-apps/loos-board-ui/* /opt/tk-dashboard/loos-board-ui/
# supervisor setup
sudo cp supervisor/tk-dashboard-loos.conf /etc/supervisor/conf.d/
sudo supervisorctl update
```