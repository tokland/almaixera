class Basket {
    constructor(options) {
        this.products = options.products;
        this.updateTotals = options.updateTotals;
    }

    init() {
        $("#products-list").addClass("products-list");
        $("#products-basket").addClass("products-basket");

        $(document).on("change", ".product .quantity", this.updateProductQuantity.bind(this));
        $(document).on("click", ".remove-product", this.removeProduct.bind(this));

        this.renderProducts();
    }

    updateProductQuantity(ev) {
        const inputEl = $(ev.currentTarget);
        const name = inputEl.attr("name");
        const strValue = inputEl.val();
        const value = parseFloat(strValue);
        const isBasket = inputEl.parents("#basket-list").length > 0;
        if (isBasket) {
            $(`[name=${name}]`)
                .not(inputEl)
                .get()
                .forEach((el) => $(el).val(strValue));
        }

        if (!isBasket || value <= 0) {
            this.renderProducts();
        }

        this.updateTotals();
    }

    removeProduct(ev) {
        const inputEl = $(ev.currentTarget);
        const id = inputEl.attr("data-product-id");
        if (!id) return;
        $(`[name=quantitat-${id}]`).val(0.0);
        this.renderProducts();
        this.updateTotals();
    }

    renderProducts() {
        const container = $("#basket-list");
        const products = $("#products-list .product .quantity")
            .get()
            .filter((input) => parseFloat(input.value) > 0)
            .map((el) => $(el).parent(".product"))
            .map((productEl) => {
                const quantity = productEl.find(".quantity").val();
                const id = productEl.find(".quantity").attr("name").split("-")[1];
                const product = this.products[id];
                return product ? { product, quantity } : null;
            })
            .filter((product) => product)
            .sort((p1, p2) => p1.provider <= p2.provider);

        if (products.length > 0) {
            container.html("");
            products.forEach((data, idx) => {
                container.append(this.renderProduct(data, idx));
            });
        } else {
            container.html("La cistella està buida");
        }
    }

    renderProduct(data, index) {
        const product = data.product;
        const quantity = data.quantity;
        const idxClass = index % 2 == 0 ? "parell" : "senar";

        return $("<div>", { id: `product-${product.id}`, class: `product ${idxClass}` }).append([
            $("<div>", { class: "prod" }).html(`<i>${product.provider}</i> - ${product.name}`),
            $("<input>", {
                class: "price",
                disabled: "disabled",
                name: `preu-${product.id}`,
                value: product.price,
            }),
            $("<input>", {
                class: "quantity",
                name: `quantitat-${product.id}`,
                type: "number",
                value: quantity,
                step: "any",
            }),
            $("<button>", {
                "aria-label": "Treu",
                class: "remove-product",
                "data-product-id": product.id,
            }).html("&#x274E;"),
        ]);
    }
}
