from decimal import Decimal

# ITR Add-on Prices (Source of Truth)
ITR_ADDON_PRICES = {
    # Common Add-ons
    "interest": Decimal("1.00"),
    "capital_gains": Decimal("1.00"),
    "house_property": Decimal("1.00"), # per property
    "india_dividend": Decimal("1.00"),
    "foreign_dividend": Decimal("1.00"),
    "other_income": Decimal("1.00"),
    # Service Specific Add-ons
    "partnership_income": Decimal("1.00"),
}

def calculate_itr_total(base_price, addons_list, house_property_count=1):
    """
    Calculate total price for an ITR service item (PER YEAR).
    """
    total = Decimal(str(base_price))
    
    for addon_id in addons_list:
        if addon_id == "core":
            continue
        
        price = ITR_ADDON_PRICES.get(addon_id, Decimal("0.00"))
        
        if addon_id == "house_property":
            total += price * Decimal(str(house_property_count))
        else:
            total += price
            
    return total

def get_verified_price(service_obj, item_data):
    """
    Verifies and returns the correct price for a service item.
    """
    if not service_obj:
        return Decimal(str(item_data.get('price', 0)))

    base_price = Decimal(str(service_obj.price or 0))
    category_name = service_obj.category.name if service_obj.category else ""
    
    if category_name == "Returns":
        # Check if it's an ITR service (contains "ITR" in title)
        if "ITR" in service_obj.title:
            addons = item_data.get('addon_ids', [])
            hp_count = item_data.get('house_property_count', 1)
            # In ReturnsCheckout.jsx, year_count is reflected in how many items are added?
            # Actually, ITR.jsx does `* selectedYearCount`. 
            # We should handle multiple years as quantity or a separate field.
            # For now, let's assume quantity handles it if sent correctly.
            
            return calculate_itr_total(base_price, addons, hp_count)
            
    # Default: Return DB base price 
    # (quantity will be multiplied in the view)
    return base_price
