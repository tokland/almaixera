Software de gestió per cooperatives de consum responsable

Install
-------

Cal instal·lar (en un virtualenv):

- django 1.7 minim

Ajustos:
- copiar django1/email_settings.sample.py a django1/email_settings.py
- Crear DB amb:
	$ python manage.py migrate
- crear superuser Django
- entrar a Django admin i canviar
	- crear nou GlobalConf
	- modificar Site i posar el nou domini
	- crear 2 grups 'coopeadmin' i 'soci'
		- soci: permisos per modificar objecte Soci
		- coopeadmin: permisos totals sobre avisos, activacions, usuaris, 
			i permisos de modificacio i eliminacio de socis (no de creacio)


Run
---

$ python manage.py runserver


Deploy
------
- clone project
- adjust permissions: folder , db.sqlite3
- configure virtualhost apache
- conf. apache virtualenv
- configure static files
- configure email_settings.py
- configure Site in admin backend to the new domain

Database Migrations
-------------------
Ja no s'utilitza South des de Django 1.7. Per tant:
$ python manage.py makemigrations
$ python manage.py migrate comandes
