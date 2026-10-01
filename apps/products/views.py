from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
)

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)

from .forms import (
    CategoryForm,
    IngredientForm,
    ProductBottlePricesForm,
    ProductForm,
    ProductIngredientFormSet,
)
from .models import (
    Product,
    ProductCategory,
)
from .services import (
    create_category,
    create_ingredient,
    create_product,
    export_products_csv,
    set_product_bottle_prices,
    update_product,
)


class CategoryListView(OwnerRequiredMixin, ListView):
    template_name = "products/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        return (
            ProductCategory.objects
            .for_user(self.request.user)
            .select_related("parent")
        )


class CategoryCreateView(OwnerRequiredMixin, CreateView):
    template_name = "products/category_form.html"
    form_class = CategoryForm
    success_url = reverse_lazy("category-list")

    def form_valid(self, form):
        try:
            create_category(
                actor=self.request.user,
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Category created successfully.",
        )

        return redirect(self.success_url)


class IngredientCreateView(OwnerRequiredMixin, CreateView):
    template_name = "products/ingredient_form.html"
    form_class = IngredientForm
    success_url = reverse_lazy("category-list")

    def form_valid(self, form):
        try:
            create_ingredient(
                actor=self.request.user,
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            "Ingredient created successfully.",
        )

        return redirect(self.success_url)


class ProductListView(OwnerRequiredMixin, ListView):
    template_name = "products/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        return (
            Product.objects
            .for_user(self.request.user)
            .select_related("category")
        )


class DistributorProductListView(DistributorRequiredMixin, ListView):
    template_name = "products/distributor_product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        return (
            Product.objects
            .for_user(self.request.user)
            .select_related("category")
            .order_by("name", "sku")
        )


class ProductDetailView(OwnerRequiredMixin, DetailView):
    template_name = "products/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return (
            Product.objects
            .for_user(self.request.user)
            .select_related("category")
            .prefetch_related("ingredients__ingredient", "bottle_prices")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bottle_price_form"] = ProductBottlePricesForm(product=self.object)
        return context


class ProductBottlePricesView(OwnerRequiredMixin, View):
    """Saves our expected Manufacturer price per bottle size — what a
    Request to Quote line is pre-filled with."""

    def post(self, request, pk):
        product = Product.objects.for_user(request.user).filter(pk=pk).first()
        if product is None:
            raise Http404

        form = ProductBottlePricesForm(request.POST, product=product)
        if not form.is_valid():
            messages.error(request, "Prices must be numbers of zero or more.")
            return redirect("product-detail", pk=pk)

        try:
            set_product_bottle_prices(
                actor=request.user, product=product, prices=form.prices()
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("product-detail", pk=pk)

        messages.success(request, "Bottle prices saved.")
        return redirect("product-detail", pk=pk)


class ProductEditorMixin:
    def get_ingredient_formset(self, *, instance, data=None):
        return ProductIngredientFormSet(
            data=data,
            instance=instance,
        )


class ProductCreateView(OwnerRequiredMixin, ProductEditorMixin, View):
    template_name = "products/product_form.html"

    def get(self, request):
        form = ProductForm()
        formset = self.get_ingredient_formset(
            instance=form.instance
        )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "ingredient_formset": formset,
            },
        )

    def post(self, request):
        form = ProductForm(
            request.POST,
            request.FILES,
        )
        formset = self.get_ingredient_formset(
            instance=form.instance,
            data=request.POST,
        )

        if not form.is_valid() or not formset.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "ingredient_formset": formset,
                },
            )

        try:
            product = create_product(
                actor=request.user,
                ingredients=ingredient_rows(formset),
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "ingredient_formset": formset,
                },
            )

        messages.success(
            request,
            "Product created successfully.",
        )

        return redirect(
            "product-detail",
            pk=product.pk,
        )


class ProductUpdateView(OwnerRequiredMixin, ProductEditorMixin, View):
    template_name = "products/product_form.html"

    def get_object(self):
        return (
            Product.objects
            .for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        product = self.get_object()

        if product is None:
            raise Http404

        form = ProductForm(
            instance=product,
        )
        formset = self.get_ingredient_formset(
            instance=product
        )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "ingredient_formset": formset,
                "object": product,
            },
        )

    def post(self, request, pk):
        product = self.get_object()

        if product is None:
            raise Http404

        form = ProductForm(
            request.POST,
            request.FILES,
            instance=product,
        )
        formset = self.get_ingredient_formset(
            instance=product,
            data=request.POST,
        )

        if not form.is_valid() or not formset.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "ingredient_formset": formset,
                    "object": product,
                },
            )

        try:
            updated = update_product(
                actor=request.user,
                product_id=product.pk,
                ingredients=ingredient_rows(formset),
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "ingredient_formset": formset,
                    "object": product,
                },
            )

        messages.success(
            request,
            "Product updated successfully.",
        )

        return redirect(
            "product-detail",
            pk=updated.pk,
        )


class ProductCSVExportView(OwnerRequiredMixin, View):
    def get(self, request):
        products = Product.objects.for_user(request.user)
        csv_content = export_products_csv(products)

        response = HttpResponse(csv_content, content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="shopify-products.csv"'
        )

        return response


def ingredient_rows(formset):
    rows = []

    for index, form in enumerate(formset.forms):
        if not form.cleaned_data:
            continue

        if form.cleaned_data.get("DELETE"):
            continue

        if not form.cleaned_data.get("ingredient"):
            continue

        row = dict(form.cleaned_data)
        row.pop("DELETE", None)
        row.setdefault("sort_order", index)
        rows.append(row)

    return rows