Software de gestió per cooperatives de consum responsable

Actualment funcionant a http://almaixera.cat

Pots entrar a una site de demo a http://almaixera-emieza.rhcloud.com (usuari/pass = admin/admin123 o bé coope/coope123)

Si t'interessa utilitzar aquest software sense haver-ho d'instal·lar contacta amb mi a emieza@xtec.cat


Install
-------

Cal instal·lar (en un virtualenv):

- django 1.7 minim

Ajustos:
- copiar django1/email_settings.sample.py a django1/email_settings.py
- Crear DB amb:
	$ python manage.py migrate
- crear superuser Django:
	$ python manage.py createsuperuser
- entrar a Django admin i canviar
	- crear nou GlobalConf
	- modificar Site i posar el nou domini
	- crear 2 grups 'coopeadmin' i 'soci'
		- soci: permisos per modificar objecte Soci
		- coopeadmin: permisos totals sobre avisos, activacions, usuaris, 
			i permisos de modificacio i eliminacio de socis (no de creacio)


Docker
------

Una sola imatge serveix per DEV i per PRO. Porta MariaDB dins del mateix
contenidor, perque django1/settings.py parla amb la base de dades per
localhost i el repo no te migracions (estan al .gitignore): l'unica manera
de tenir una base de dades es restaurar un dump.

1) Construir la imatge:

	$ docker/build.sh

2) Arrencar el contenidor, passant el dump de partida:

	$ docker/start.sh rostoll-db.sql.gz

	http://localhost:5000/

Opcions de docker/start.sh:

	-m, --mode dev|prod   dev = runserver amb DEBUG i el codi del working
	                      copy muntat (recarrega en calent); prod = uwsgi
	                      amb el codi de la imatge (per defecte: dev)
	-p, --port PORT       port publicat a la maquina (per defecte: 5000)
	-n, --name NAME       nom del contenidor (per defecte: almaixera-<mode>)
	-r, --reload-db       torna a restaurar el dump encara que el volum ja
	                      estigues carregat (esborra la base de dades actual)
	-f, --foreground      no fa detach

Exemples:

	$ docker/start.sh --mode prod --port 5000 rostoll-db.sql.gz
	$ docker/start.sh --reload-db rostoll-db.sql.gz
	$ docker logs -f almaixera-dev
	$ docker exec -it almaixera-dev bash

### El dump va al start, no al build

El fitxer .sql.gz es un argument de docker/start.sh i mai entra a la imatge
(el .dockerignore exclou *.sql.gz). Els motius:

- El dump porta dades reals dels socis. Les capes d'una imatge es queden per
  sempre encara que despres s'esborri el fitxer, i les imatges es pugen i es
  comparteixen.
- DEV i PRO volen dumps diferents a partir de la mateixa imatge.
- Refrescar la base de dades no hauria d'obligar a reconstruir la imatge.

La restauracio es fa nomes el primer cop: el dump es carrega dins un volum
(almaixera-<mode>-db) i queda marcat. Els reinicis conserven la base de dades
tal com hagi quedat; amb --reload-db es torna a carregar des de zero.

### Variables d'entorn

	DJANGO_DEBUG=1        activa DEBUG (ho posa el mode dev)
	DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
	                      per defecte rostoll/root/5jitk9/localhost/3306,
	                      igual que a produccio

### email_settings.py

No esta al repositori. La imatge hi posa el sample com a fallback; en mode
prod, si tens django1/email_settings.py al working copy, docker/start.sh el
munta a sobre perque els correus surtin de veritat.


API de productes
----------------

REST amb Django REST Framework (djangorestframework==3.11.2, l'ultima versio
que suporta Django 2.1). No ve amb Django: es un paquet a part, ja inclos al
requirements.txt.

	GET    /comandes/api/productes/          llistat (paginat de 50 en 50)
	GET    /comandes/api/productes/<id>/     un producte
	POST   /comandes/api/productes/          upsert: crea-o-actualitza (un o una llista)
	DELETE /comandes/api/productes/<id>/     baixa (actiu=False)
	DELETE /comandes/api/productes/<id>/?force=1   esborrat de veritat

Filtres del llistat: ?q= (nom o proveidor), ?proveidor=<id>, ?actiu=,
?stock=, ?granel=. Paginacio: ?page=.

Autenticacio: sessio (la mateixa del web, i el navegador et deixa fer-hi
clics a /comandes/api/productes/) o HTTP basic per scripts:

	$ curl -u usuari:contrasenya http://localhost:5000/comandes/api/productes/?q=poma

### Alta en bloc

El mateix POST serveix per a un producte o per a molts: si el cos es un
objecte en dona d'alta un, i si es una llista els dona d'alta tots.

	$ curl -u usuari:contrasenya -X POST \
	    http://localhost:5000/comandes/api/productes/ \
	    -H 'Content-Type: application/json' \
	    -d '[{"nom":"POMA GOLDEN","proveidor":1,"preu":"2.40","granel":true},
	         {"nom":"PERA CONFERENCE","proveidor":1,"preu":"2.90"}]'

Es tot o res: primer es valida tota la llista i, si un element falla, no se'n
crea cap. La resposta d'error es una llista de la mateixa llargada amb
l'element que falla marcat:

	[{}, {}, {"preu": ["No pot haver-hi mes de 5 digits en total."]}]

Camps: nom i proveidor son obligatoris; actiu (per defecte true), preu (per
defecte 0, maxim 999.99), stock, granel (per defecte true) i notes son
optatius. Un usuari proveidor nomes pot donar d'alta productes seus: el camp
proveidor se li fixa al seu, tal com nomes en pot veure els seus.

### Upsert (crea-o-actualitza) per codi

El POST no crea duplicats a cada sincronitzacio: es un upsert. Si el camp
'notes' porta el marcador `ecoplaza-sku:<codi>` i ja existeix un producte del
MATEIX proveidor amb aquell mateix codi, s'ACTUALITZEN els seus camps enlloc
de crear-ne un de nou; si no existeix, es crea. El codi (la part despres del
marcador) es la clau natural, no cal enviar cap id.

	notes = "ecoplaza-sku: 00047\nEAN: 8414652066017"

La resposta torna 201 si s'ha creat algun producte i 200 si nomes se n'han
actualitzat. Aixo permet re-executar la mateixa importacio tantes vegades com
calgui: els productes sense canvis queden igual, els que canvien s'actualitzen
al lloc (mantenen el mateix id i les linies de comanda associades) i els que
ja no venen a l'origen es donen de baixa amb DELETE (actiu=False). Veure
comandes/api.py (create() i _upsert_un()).

### Permisos

Llegir (GET) el pot fer qualsevol usuari identificat. Donar d'alta (POST) i
esborrar (DELETE) demanen els permisos comandes.add_producte i
comandes.delete_producte, els mateixos que governen l'alta i la baixa al
Django admin.

### Per que DELETE desactiva enlloc d'esborrar

Producte es el desti de tres ForeignKey amb on_delete=CASCADE
(DetallComanda, ComandaStock, ActivaProducte). Un esborrat real s'emporta les
linies de comandes ja fetes i, per tant, canvia informes antics
(informe_proveidors, informe_caixes). Com que la coope ja fa servir el camp
'actiu' com a baixa logica, el DELETE per defecte nomes desactiva i la
resposta diu quantes files s'emportaria un esborrat real:

	{"id": 7195, "esborrat": false, "actiu": false,
	 "afectats": {"detalls_comanda": 1, "comandes_stock": 0, "activacions": 2}}

Amb ?force=1 s'esborra de veritat.

### Visibilitat

L'API ensenya exactament els mateixos productes que el Django admin
(comandes.helpers.productes_visibles, que ara fan servir els dos): els
administradors ho veuen tot, un usuari proveidor nomes els seus productes, i
la resta nomes els productes de proveidors sense usuari.


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
