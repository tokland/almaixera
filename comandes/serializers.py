# -*- coding: utf-8 -*-
from rest_framework import serializers

from .models import Producte


class ProducteSerializer(serializers.ModelSerializer):
    proveidor_nom = serializers.CharField(source='proveidor.nom', read_only=True)
    # El que es veu als desplegables de la comanda, per poder identificar el
    # producte sense haver de fer una segona crida.
    label = serializers.CharField(source='__str__', read_only=True)

    class Meta:
        model = Producte
        fields = (
            'id', 'nom', 'label', 'actiu', 'preu',
            'stock', 'granel', 'notes',
            'proveidor', 'proveidor_nom',
        )
