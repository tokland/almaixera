import itertools
from datetime import datetime
from functools import wraps

from django.http import HttpResponse
from django.views import View
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.forms.models import modelformset_factory
from django.forms import ModelForm
from django.db.models import Sum
from django.core.exceptions import FieldError

from comandes.models import DetallComanda

def soci_required(fn):
    @wraps(fn)
    def wrap(request, *args, **kwargs):
        current_user_is_soci = hasattr(request.user, 'soci')
        if current_user_is_soci:
            return fn(request, *args, **kwargs)
        else:
            return render(request, 'nosoci.html')
    return login_required(wrap)

class DetallComandaForm(ModelForm):
    class Meta:
        model = DetallComanda
        fields = ["quantitat_rebuda"]

def get_detalls(date, coope):
    return (DetallComanda.objects.
        filter(
            comanda__data_recollida=date,
            comanda__soci__cooperativa=coope,
        ).order_by(
            'producte__nom',
            'producte__id',
            'comanda__soci__num_caixa',
        ))

def get_forms_by_product(formset, detalls):
    def get_product_from_form(form):
        detall_comanda_id = form["id"].value()
        detall_comanda = DetallComanda.objects.get(id=detall_comanda_id)
        return detall_comanda.producte

    def get_info(detall_comanda_id):
        detall = DetallComanda.objects.get(id=detall_comanda_id)
        return dict(
            id=detall.id,
            num_caixa=detall.comanda.soci.num_caixa,
            soci= "({}) {} {}".format(
                detall.comanda.soci.user.username,
                detall.comanda.soci.user.first_name,
                detall.comanda.soci.user.last_name,
            ),
            quant=detall.quantitat,
            preu=detall.producte.preu,
            subtotal=float(detall.quantitat_rebuda) * float(detall.producte.preu),
        )

    def get_total(detalls, product):
        aggr = detalls.filter(producte__id=product.id).aggregate(Sum('quantitat'))
        return aggr["quantitat__sum"]

    def get_data(forms):
        return [dict(form=form, info=get_info(form['id'].value())) for form in forms]

    groups = itertools.groupby(formset, get_product_from_form)
    return [(product, get_total(detalls, product), get_data(forms)) for (product, forms) in groups]

class BoxesReportView(View):
    def render(self, request, get_formset):
        string_date = request.GET.get('data_informe')
        try:
            date = datetime.strptime(string_date, "%Y-%m-%d")
        except:
            raise FieldError("Date error")
        coope = request.user.soci.cooperativa
        detalls = get_detalls(date, coope)
        FormsetClass = modelformset_factory(DetallComanda, form=DetallComandaForm, extra=0)
        formset = get_formset(FormsetClass, detalls)
        forms_by_product = get_forms_by_product(formset, detalls)

        return render(request, 'boxes_report.html', dict(
            date=date,
            forms_by_product=forms_by_product,
            cooperative=coope,
            formset=formset,
        ))

    @method_decorator(soci_required)
    def get(self, request):
        def get_formset_from_db(FormsetClass, detalls):
            return FormsetClass(queryset=detalls)
        return self.render(request, get_formset_from_db)

    @method_decorator(soci_required)
    def post(self, request):
        # TODO: date as query string, because this comes from a form GET
        # Or use a redirection, example: https://goo.gl/BMrpZj
        def get_persisted_formset_from_request(FormsetClass, _detalls):
            formset = FormsetClass(request.POST)
            if formset.is_valid():
                formset.save()
            return formset
        return self.render(request, get_persisted_formset_from_request)
