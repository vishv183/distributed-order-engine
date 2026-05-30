"""
DataCo Smart Supply Chain Dataset Importer
===========================================
Parses the DataCo CSV and transforms it into the B2B Exception Engine schema.
"""

import csv
import json
import os
import random
from decimal import Decimal

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import get_settings
from backend.app.models.db import Account, AccountTier, Base, Inventory, Order, OrderStatus, WarehouseCode

settings = get_settings()

# Connect to the MAIN database used by the FastAPI app and Celery worker
# By default, config uses postgresql+psycopg2://...
engine = create_engine(settings.SYNC_DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def map_tier(segment: str) -> AccountTier:
    if segment == "Corporate":
        return AccountTier.WHOLESALE
    elif segment == "Home Office":
        return AccountTier.VIP
    return AccountTier.STANDARD

def import_data(csv_path: str, row_limit: int = 10000):
    print(f"Loading {row_limit} rows from {csv_path} into {settings.SYNC_DATABASE_URL} ...")
    
    # Ensure tables exist
    Base.metadata.create_all(engine)
    session = SessionLocal()

    # Clear existing data so we don't duplicate on multiple runs
    print("Clearing old data...")
    session.execute(Order.__table__.delete())
    session.execute(Inventory.__table__.delete())
    session.execute(Account.__table__.delete())
    session.commit()

    accounts_map = {}  # Customer Id -> internal account id
    inventory_map = set() # Track SKUs we've already added

    accounts_to_insert = []
    inventory_to_insert = []
    orders_to_insert = []

    with open(csv_path, 'r', encoding='latin1') as f:
        reader = csv.DictReader(f)
        
        count = 0
        for row in reader:
            if count >= row_limit:
                break
                
            customer_id = row['Customer Id']
            sku = row['Product Name'].strip()
            
            # 1. Build Accounts
            if customer_id not in accounts_map:
                # We assign a temporary incremental ID for foreign keys
                internal_id = len(accounts_map) + 1
                accounts_map[customer_id] = internal_id
                
                accounts_to_insert.append({
                    "id": internal_id,
                    "company_name": f"{row['Customer Fname']} {row['Customer Lname']} ({row['Customer City']})",
                    "tier": map_tier(row['Customer Segment'])
                })

            # 2. Build Inventory (assign random stock across 2 warehouses)
            if sku not in inventory_map:
                inventory_map.add(sku)
                inventory_to_insert.append({
                    "sku": sku,
                    "warehouse_code": WarehouseCode.WH_A,
                    "quantity": random.randint(10, 500)
                })
                inventory_to_insert.append({
                    "sku": sku,
                    "warehouse_code": WarehouseCode.WH_B,
                    "quantity": random.randint(0, 300)
                })

            # 3. Build Orders
            # We will artificially create exceptions if the 'Delivery Status' is problematic
            delivery_status = row['Delivery Status']
            status = OrderStatus.PENDING
            error_log = None
            
            if delivery_status == 'Late delivery':
                status = OrderStatus.EXCEPTIONAL_HOLD
                error_log = json.dumps({
                    "reason": "Late delivery risk identified",
                    "scheduled_days": row['Days for shipment (scheduled)'],
                    "real_days": row['Days for shipping (real)'],
                    "shipping_mode": row['Shipping Mode']
                })
            elif delivery_status == 'Shipping canceled':
                status = OrderStatus.EXCEPTIONAL_HOLD
                error_log = json.dumps({"reason": "Carrier canceled shipping route"})
                
            qty = int(row['Order Item Quantity'])
            total = Decimal(str(round(float(row['Order Item Total']), 2)))
            
            orders_to_insert.append({
                "account_id": accounts_map[customer_id],
                "sku": sku,
                "ordered_quantity": qty,
                "calculated_total": total,
                "status": status,
                "error_log": error_log
            })
            
            count += 1

    print(f"Parsed {len(accounts_to_insert)} Accounts, {len(inventory_map)} unique SKUs, {len(orders_to_insert)} Orders.")
    
    # Chunk inserts
    chunk_size = 2000
    
    print("Inserting Accounts...")
    for i in range(0, len(accounts_to_insert), chunk_size):
        session.execute(insert(Account).values(accounts_to_insert[i:i+chunk_size]))
    session.commit()

    print("Inserting Inventory...")
    for i in range(0, len(inventory_to_insert), chunk_size):
        session.execute(insert(Inventory).values(inventory_to_insert[i:i+chunk_size]))
    session.commit()

    print("Inserting Orders...")
    for i in range(0, len(orders_to_insert), chunk_size):
        session.execute(insert(Order).values(orders_to_insert[i:i+chunk_size]))
    session.commit()
    
    session.close()
    print("Import complete! Your dashboard is now populated with real DataCo supply chain data.")

if __name__ == "__main__":
    csv_file = "datasets/DataCoSupplyChainDataset.csv"
    if not os.path.exists(csv_file):
        print(f"Error: Could not find {csv_file}")
    else:
        # Load 10,000 rows from the dataset
        import_data(csv_file, row_limit=10000)
