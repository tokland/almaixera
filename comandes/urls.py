from django.conf.urls import patterns, url
from django.contrib.auth.views import password_reset, \
	password_reset_done, password_reset_confirm, \
    password_reset_complete, login, logout


from comandes import views

urlpatterns = patterns('',
    url(r'^$', views.index, name='index'),
    url(r'^fer_comanda', views.fer_comanda, name='fer_comanda'),
    url(r'^esborra_comanda', views.esborra_comanda, name='esborra_comanda'),
    url(r'^comandes', views.veure_comandes, name='comandes'),
    url(r'^informe_proveidors', views.informe_proveidors, name='informe_proveidors'),
    url(r'^informe_caixes', views.informe_caixes, name='informe_caixes'),
    url(r'^test_email', views.test_email, name="test_email"),
    url(r'^recuperar_contrasenya/$', password_reset, name="recuperar_contrasenya"),
    url(r'^password_reset/$', password_reset, name="password_reset"),
    url(r'^password_reset_done/$', password_reset_done, name="password_reset_done"),
    url(r'^password_reset_confirm/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$', password_reset_confirm, name="password_reset_confirm"),
    url(r'^password_reset_complete/$', password_reset_complete, name="password_reset_complete"),
    url(r'^afegeix_proveidors/', views.afegeix_proveidors, name="afegeix_proveidors" ),
    url(r'^distribueix_productes/(?P<data_recollida>[0-9-]+)/(?P<producte>\w{0,50})/$', views.distribueix_productes, name="distribueix_productes" ),
    url(r'^accounts/login/$', login,{'template_name': 'login.html'}),
    url(r'^accounts/logout/$', logout),
    #{'next_page': '/'}),
)

#http://stackoverflow.com/questions/21284672/django-password-reset-password-reset-confirm
# change SITE_ID in settings.py
