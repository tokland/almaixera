# -*- coding: utf-8 -*-
"""API REST de productes (llistar, consultar, crear/actualitzar, esborrar).

Muntada a /comandes/api/productes/ -- veure comandes/urls.py.
"""
import re

from django.db import transaction
from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response

from .helpers import productes_visibles
from .models import ActivaProducte, ComandaStock, DetallComanda, Producte
from .serializers import ProducteSerializer


def _parse_bool(value):
    if value is None:
        return None
    return value.lower() in ('1', 'true', 'yes', 'si', 'sí')


# El client de sincronitzacio guarda el codi d'article d'origen dins 'notes'
# darrere aquest marcador; el fem servir com a clau natural per a l'upsert.
SKU_MARKER = 'ecoplaza-sku:'
_SKU_RE = re.compile(re.escape(SKU_MARKER) + r'\s*(\S+)')


def _parse_sku(notes):
    if not notes:
        return None
    match = _SKU_RE.search(notes)
    return match.group(1) if match else None


class ProducteViewSet(mixins.ListModelMixin,
                      mixins.RetrieveModelMixin,
                      mixins.CreateModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    """
    list:     GET    /comandes/api/productes/
              filtres: ?q= ?proveidor= ?actiu= ?stock= ?granel=
    retrieve: GET    /comandes/api/productes/<id>/
    create:   POST   /comandes/api/productes/
              Upsert (crea-o-actualitza). El cos pot ser un objecte (un
              producte) o una llista (en bloc). Si el 'notes' porta el
              marcador 'ecoplaza-sku:<codi>' i ja existeix un producte del
              mateix proveidor amb aquell codi, s'ACTUALITZA enlloc de crear
              un duplicat. Retorna 201 si s'ha creat res, 200 si nomes
              s'han actualitzat. Veure create()/_upsert_un().
    destroy:  DELETE /comandes/api/productes/<id>/[?force=1]

    Per defecte DELETE desactiva el producte (actiu=False) enlloc
    d'esborrar-lo. Veure destroy().
    """
    serializer_class = ProducteSerializer
    # GET queda per a qualsevol usuari identificat; POST i DELETE demanen els
    # permisos comandes.add_producte / comandes.delete_producte, els mateixos
    # que ja governen l'alta i la baixa al Django admin.
    permission_classes = (DjangoModelPermissions,)

    def get_queryset(self):
        # Producte.ordering es un atribut de classe solt, no un Meta.ordering,
        # o sigui que no ordena res: cal ordenar aqui perque la paginacio sigui
        # estable.
        qs = productes_visibles(self.request.user).select_related('proveidor')
        params = self.request.query_params

        cerca = params.get('q')
        if cerca:
            qs = qs.filter(Q(nom__icontains=cerca) | Q(proveidor__nom__icontains=cerca))

        proveidor = params.get('proveidor')
        if proveidor:
            qs = qs.filter(proveidor=proveidor)

        for camp in ('actiu', 'stock', 'granel'):
            valor = _parse_bool(params.get(camp))
            if valor is not None:
                qs = qs.filter(**{camp: valor})

        return qs.order_by('nom', 'id')

    def get_serializer(self, *args, **kwargs):
        """Accepta un producte o una llista de productes al POST.

        DRF ja sap serialitzar llistes amb many=True; nomes cal decidir-ho
        segons la forma del cos de la peticio.
        """
        if isinstance(kwargs.get('data'), list):
            kwargs['many'] = True
        return super(ProducteViewSet, self).get_serializer(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        """Upsert individual o en bloc, tot o res.

        Amb many=True el serializer valida TOTA la llista abans de tocar res,
        i la transaccio fa que un error a l'ultim element no deixi a mitges
        els anteriors. Cada element s'actualitza si ja existeix (mateix
        proveidor + mateix codi 'ecoplaza-sku:') o es crea si no.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data
        many = isinstance(validated, list)
        items = validated if many else [validated]

        user = self.request.user
        proveidor_forcat = getattr(user, 'proveidor', None)
        forcar = proveidor_forcat is not None and not user.is_superuser

        instancies = []
        algun_creat = False
        with transaction.atomic():
            for dades in items:
                if forcar:
                    # un usuari proveidor nomes pot tocar productes seus
                    dades = dict(dades)
                    dades['proveidor'] = proveidor_forcat
                instancia, creat = self._upsert_un(dades)
                instancies.append(instancia)
                algun_creat = algun_creat or creat

        sortida = ProducteSerializer(
            instancies, many=True, context=self.get_serializer_context())
        cos = sortida.data if many else sortida.data[0]
        codi = status.HTTP_201_CREATED if algun_creat else status.HTTP_200_OK
        return Response(cos, status=codi)

    def _upsert_un(self, dades):
        """Crea o actualitza un producte segons el codi 'ecoplaza-sku:'.

        Retorna (instancia, creat_bool). El marcador es la clau natural: es
        busca un producte del mateix proveidor amb el mateix codi. El filtre
        __contains nomes acota; despres es compara el codi exacte per no
        confondre '0004' amb '00047'.
        """
        proveidor = dades.get('proveidor')
        sku = _parse_sku(dades.get('notes') or '')

        existent = None
        if sku and proveidor is not None:
            candidats = Producte.objects.filter(
                proveidor=proveidor,
                notes__contains='%s %s' % (SKU_MARKER, sku),
            )
            for candidat in candidats:
                if _parse_sku(candidat.notes) == sku:
                    existent = candidat
                    break

        if existent is None:
            return Producte.objects.create(**dades), True

        for camp, valor in dades.items():
            setattr(existent, camp, valor)
        existent.save()
        return existent, False

    def destroy(self, request, *args, **kwargs):
        """Desactiva el producte; amb ?force=1 l'esborra de veritat.

        Producte es el desti de tres FK amb on_delete=CASCADE
        (DetallComanda, ComandaStock, ActivaProducte), o sigui que un DELETE
        real s'emporta les linies de comandes ja fetes i canvia informes
        antics. La coope ja fa servir el camp 'actiu' com a baixa logica.
        """
        producte = self.get_object()
        afectats = {
            'detalls_comanda': DetallComanda.objects.filter(producte=producte).count(),
            'comandes_stock': ComandaStock.objects.filter(producte=producte).count(),
            'activacions': ActivaProducte.objects.filter(producte=producte).count(),
        }

        if _parse_bool(request.query_params.get('force')):
            producte.delete()
            return Response(
                {'id': kwargs.get('pk'), 'esborrat': True, 'afectats': afectats},
                status=status.HTTP_200_OK,
            )

        producte.actiu = False
        producte.save(update_fields=['actiu'])
        return Response(
            {
                'id': producte.id,
                'esborrat': False,
                'actiu': False,
                'afectats': afectats,
                'detall': "Producte desactivat. Fes servir ?force=1 per esborrar-lo "
                          "de veritat (arrossega les linies de comanda indicades a 'afectats').",
            },
            status=status.HTTP_200_OK,
        )
