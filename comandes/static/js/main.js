function boxesReportUpdates() {
    $("[data-quantity] input").change(ev => {
        const input = $(ev.currentTarget);
        const quantity = parseFloat(input.val());
        const el = $(ev.currentTarget).parent("[data-quantity]");
        el.parent("tr").css("background-color", "#CFA");
        const id = el.attr("data-quantity");
        const priceString = $(`[data-price='${id}']`).text()
        const price = parseFloat(priceString.replace(".", "").replace(",", "."));
        const subtotal = quantity && price ? (quantity * price).toFixed(2) : "";
        $(`[data-subtotal='${id}']`).text(subtotal);
    });
}
