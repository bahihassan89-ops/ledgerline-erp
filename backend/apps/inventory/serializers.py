from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer

from .models import Product, ProductCategory, StockMove, StockQuant, Warehouse


class ProductCategorySerializer(CompanyScopedSerializer):
    class Meta:
        model = ProductCategory
        fields = ["id", "name", "parent", "cost_method", "inventory_account", "cogs_account", "income_account",
                  "expense_account"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and "cost_method" in attrs and attrs["cost_method"] != self.instance.cost_method:
            from .models import ProductValuation
            if ProductValuation.objects.filter(product__category=self.instance, quantity__gt=0).exists():
                raise serializers.ValidationError({"cost_method": "Products in this category have stock; the "
                                                                  "costing method cannot change."})
        checks = {"inventory_account": "inventory", "cogs_account": "cost_of_sales"}
        for f, t in checks.items():
            acc = attrs.get(f)
            if acc and acc.account_type != t:
                raise serializers.ValidationError({f: f"Must be a '{t}' account."})
        return attrs


class ProductSerializer(CompanyScopedSerializer):
    qty_on_hand = serializers.SerializerMethodField()
    average_cost = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "barcode", "type", "category", "category_name", "uom", "sale_price",
                  "purchase_price", "sale_tax", "purchase_tax", "income_account", "expense_account", "reorder_level",
                  "active", "qty_on_hand", "average_cost"]

    def get_qty_on_hand(self, obj):
        try:
            return str(obj.valuation.quantity)
        except Product.valuation.RelatedObjectDoesNotExist:
            return "0"

    def get_average_cost(self, obj):
        try:
            return str(round(obj.valuation.average_cost, 4))
        except Product.valuation.RelatedObjectDoesNotExist:
            return "0"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and "type" in attrs and attrs["type"] != self.instance.type and \
                self.instance.moves.exists():
            raise serializers.ValidationError({"type": "Product has stock moves; its type cannot change."})
        return attrs


class WarehouseSerializer(CompanyScopedSerializer):
    class Meta:
        model = Warehouse
        fields = ["id", "code", "name", "address", "active"]


class StockMoveSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    entry_number = serializers.CharField(source="journal_entry.number", read_only=True, default=None)

    class Meta:
        model = StockMove
        fields = ["id", "number", "date", "move_type", "product", "product_sku", "product_name", "warehouse",
                  "warehouse_code", "quantity", "unit_cost", "value", "partner", "reference", "journal_entry",
                  "entry_number"]


class StockQuantSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)

    class Meta:
        model = StockQuant
        fields = ["id", "product", "product_sku", "product_name", "warehouse", "warehouse_code", "quantity"]
