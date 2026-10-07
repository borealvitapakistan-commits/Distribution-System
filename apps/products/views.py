import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import linebreaks
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

from . import shopify, shopify_import
from .catalog import category_style, size_label, unit_label, unit_plural
from .forms import (
    CategoryForm,
    IngredientForm,
    ProductBottlePricesForm,
    ProductForm,
    ProductIngredientFormSet,
    ProductPackagePriceForm,
    ProductRetailPricesForm,
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
    export_retail_price_sheet,
    set_product_bottle_prices,
    delete_package_price,
    save_package_price,
    set_product_retail_prices,
    update_product,
    update_product_photos,
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

    SORTS = {
        "name": ("Product Name", ("name_lower", "sku")),
        "sku": ("SKU", ("sku",)),
        "category": ("Category", ("category__sort_order", "category__name", "name_lower")),
        "unit": ("Unit", ("unit_of_measure", "name_lower")),
        "size": ("Size", ("size", "name_lower")),
        "status": ("Status", ("-active", "name_lower")),
    }

    def _params(self):
        params = self.request.GET
        sort = params.get("sort", "name")

        if sort.lstrip("-") not in self.SORTS:
            sort = "name"

        return {
            "q": params.get("q", "").strip(),
            "category": params.get("category", ""),
            "status": params.get("status", ""),
            "sort": sort,
        }

    def _all_products(self):
        return Product.objects.for_user(self.request.user)

    def get_queryset(self):
        params = self._params()
        queryset = (
            self._all_products()
            .select_related("category")
            .prefetch_related("package_prices")
            .annotate(name_lower=Lower("name"))
        )

        if params["q"]:
            q = params["q"]
            queryset = queryset.filter(
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(category__name__icontains=q)
            )

        if params["category"] == "none":
            queryset = queryset.filter(category__isnull=True)
        elif params["category"]:
            try:
                category_id = uuid.UUID(params["category"])
            except ValueError:
                return queryset.none()
            queryset = queryset.filter(category_id=category_id)

        if params["status"] == "active":
            queryset = queryset.filter(active=True)
        elif params["status"] == "inactive":
            queryset = queryset.filter(active=False)

        sort = params["sort"]
        ordering = self.SORTS[sort.lstrip("-")][1]

        if sort.startswith("-"):
            ordering = [
                field[1:] if field.startswith("-") else f"-{field}"
                for field in ordering
            ]

        return queryset.order_by(*ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self._params()

        for product in context["products"]:
            product.tone, product.icon = category_style(product.category)
            product.unit_label = unit_label(product)
            product.size_label = size_label(product)

            amounts = sorted({package.amount for package in product.package_prices.all()})
            if amounts and not product.size_label:
                product.size_label = " · ".join(
                    f"{amount.normalize():f}" for amount in amounts
                ) + f" {unit_plural(product)}"

        counts = dict(
            self._all_products()
            .order_by()
            .values_list("category")
            .annotate(total=Count("id"))
        )
        tiles = []

        for category in ProductCategory.objects.for_user(self.request.user).filter(
            active=True, parent__isnull=True
        ):
            tone, icon = category_style(category)
            tiles.append(
                {
                    "value": str(category.pk),
                    "name": category.name,
                    "count": counts.get(category.pk, 0),
                    "tone": tone,
                    "icon": icon,
                }
            )

        if counts.get(None):
            tiles.append(
                {
                    "value": "none",
                    "name": "Uncategorized",
                    "count": counts[None],
                    "tone": "slate",
                    "icon": "box",
                }
            )

        current = params["sort"]
        columns = {}

        for key in self.SORTS:
            direction = ""
            if current == key:
                direction = "asc"
            elif current == f"-{key}":
                direction = "desc"

            columns[key] = {
                "next": f"-{key}" if direction == "asc" else key,
                "direction": direction,
            }

        context.update(
            {
                "params": params,
                "tiles": tiles,
                "total_count": sum(counts.values()),
                "columns": columns,
                "sort_options": [
                    (key, label) for key, (label, _fields) in self.SORTS.items()
                ],
                "current_sort": current.lstrip("-"),
            }
        )
        return context


class RetailPriceSheetView(OwnerRequiredMixin, View):
    """Downloads the retail price sheet (CSV, opens in Excel)."""

    def get(self, request):
        response = HttpResponse(
            export_retail_price_sheet(Product.objects.for_user(request.user)),
            content_type="text/csv",
        )
        response["Content-Disposition"] = 'attachment; filename="retail-price-sheet.csv"'
        return response


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
            .prefetch_related(
                "ingredients__ingredient",
                "bottle_prices",
                "retail_prices",
                "gallery",
                "package_prices__manufacturer",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object
        product.tone, product.icon = category_style(product.category)
        product.unit_label = unit_label(product)
        product.unit_plural = unit_plural(product)

        packages = list(product.package_prices.all())
        for number, package in enumerate(packages, start=1):
            package.number = number
            package.starts_group = number > 1 and package.amount != packages[number - 2].amount

        photos = [product.image] if product.image else []
        photos += [photo.image for photo in product.gallery.all()]

        description = product.body_html.strip()
        if description and "<" not in description:
            description = linebreaks(description, autoescape=True)

        context.update(
            {
                "packages": packages,
                "photos": photos,
                "description": description,
                "benefits": [
                    line.strip().lstrip("-•*").strip()
                    for line in product.key_benefits.splitlines()
                    if line.strip()
                ],
                "bottle_price_form": ProductBottlePricesForm(product=product),
                "retail_price_form": ProductRetailPricesForm(product=product),
            }
        )
        return context


class ProductPackageFormView(OwnerRequiredMixin, View):
    """Add (no package_pk) or edit one manufacturer's package size and
    price for a product."""

    template_name = "products/package_form.html"

    def _load(self, pk, package_pk):
        product = Product.objects.for_user(self.request.user).filter(pk=pk).first()
        if product is None:
            raise Http404

        package = None
        if package_pk is not None:
            package = product.package_prices.filter(pk=package_pk).first()
            if package is None:
                raise Http404

        return product, package

    def _render(self, request, form, product, package):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "product": product,
                "package": package,
                "unit": unit_plural(product),
            },
        )

    def get(self, request, pk, package_pk=None):
        product, package = self._load(pk, package_pk)
        initial = {}
        if package is not None:
            initial = {
                "manufacturer": package.manufacturer_id,
                "amount": package.amount,
                "price": package.price,
            }
        return self._render(request, ProductPackagePriceForm(initial=initial), product, package)

    def post(self, request, pk, package_pk=None):
        product, package = self._load(pk, package_pk)
        form = ProductPackagePriceForm(request.POST)

        if not form.is_valid():
            return self._render(request, form, product, package)

        try:
            save_package_price(
                actor=request.user,
                product=product,
                package=package,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, form, product, package)

        messages.success(request, "Package size saved.")
        return redirect("product-detail", pk=product.pk)


class ProductPackageDeleteView(OwnerRequiredMixin, View):
    def post(self, request, pk, package_pk):
        product = Product.objects.for_user(request.user).filter(pk=pk).first()
        package = product and product.package_prices.filter(pk=package_pk).first()
        if package is None:
            raise Http404

        delete_package_price(actor=request.user, package=package)
        messages.success(request, "Package size removed.")
        return redirect("product-detail", pk=pk)


class ProductRetailPricesView(OwnerRequiredMixin, View):
    """Saves what we sell one bottle for at each bottle size."""

    def post(self, request, pk):
        product = Product.objects.for_user(request.user).filter(pk=pk).first()
        if product is None:
            raise Http404

        form = ProductRetailPricesForm(request.POST, product=product)
        if not form.is_valid():
            messages.error(request, "Prices must be numbers of zero or more.")
            return redirect("product-detail", pk=pk)

        try:
            set_product_retail_prices(
                actor=request.user, product=product, prices=form.prices()
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("product-detail", pk=pk)

        messages.success(request, "Retail prices saved.")
        return redirect("product-detail", pk=pk)


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

        data, add_photos, remove_photos = form.split_photo_data()

        try:
            with transaction.atomic():
                product = create_product(
                    actor=request.user,
                    ingredients=ingredient_rows(formset),
                    **data,
                )
                update_product_photos(
                    actor=request.user, product=product, add=add_photos, remove=remove_photos
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

        data, add_photos, remove_photos = form.split_photo_data()

        try:
            with transaction.atomic():
                updated = update_product(
                    actor=request.user,
                    product_id=product.pk,
                    ingredients=ingredient_rows(formset),
                    **data,
                )
                update_product_photos(
                    actor=request.user, product=updated, add=add_photos, remove=remove_photos
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

class ShopifyImportView(OwnerRequiredMixin, View):
    """Shows every product in the Shopify store next to what importing
    it would do (create new / update a matching product); the Owner
    ticks the ones to bring in. Shopify is only read."""

    template_name = "products/shopify_import.html"

    def _fetch(self, request):
        if not shopify.is_configured():
            raise shopify.ShopifyError(
                "Shopify isn't connected. Add SHOPIFY_STORE_DOMAIN, "
                "SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET to the .env file."
            )
        return shopify.fetch_products()

    def get(self, request):
        products = Product.objects.for_user(request.user).order_by("name")

        try:
            shopify_products = self._fetch(request)
        except shopify.ShopifyError as exc:
            return render(request, self.template_name, {"error": str(exc)})

        rows = shopify_import.preview(shopify_products, products)

        return render(
            request,
            self.template_name,
            {
                "rows": rows,
                "products": products,
                "new_count": sum(1 for row in rows if row["match"] is None),
                "update_count": sum(1 for row in rows if row["match"] is not None),
                "store": settings.SHOPIFY_STORE_DOMAIN,
            },
        )

    def post(self, request):
        try:
            shopify_products = self._fetch(request)
        except shopify.ShopifyError as exc:
            messages.error(request, str(exc))
            return redirect("product-shopify-import")

        choices = {
            shopify_id: request.POST.get(f"target_{shopify_id}", "new")
            for shopify_id in request.POST.getlist("import")
        }

        if not choices:
            messages.error(request, "Tick at least one product to import.")
            return redirect("product-shopify-import")

        created, updated, errors = shopify_import.import_selected(
            actor=request.user,
            shopify_products=shopify_products,
            choices=choices,
        )

        if created or updated:
            messages.success(
                request,
                f"Imported from Shopify: {created} new, {updated} updated.",
            )

        for title, error in errors:
            messages.error(request, f"{title}: {error}")

        return redirect("product-list")
