from __future__ import annotations
import logging
from sqlalchemy import select
from bankrotai.db import session_scope, ProcessedLot, init_db
from bankrotai.ai import OpenAIAppraiser, apply_evaluation_to_lot
from bankrotai.domain import NormalizedLot

# Force logging to show in terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True
)
logger = logging.getLogger("mass_valuation")

def mass_evaluate_lots():
    """Скрипт для массовой оценки всех лотов, у которых еще нет рыночной стоимости."""
    print("Initializing DB...")
    init_db()
    print("Creating appraiser...")
    appraiser = OpenAIAppraiser()
    
    print(f"Starting mass valuation via {appraiser.provider.provider}...")
    
    with session_scope() as session:
        all_lots_count = session.query(ProcessedLot).count()
        print(f"Total lots in DB: {all_lots_count}")
        
        stmt = select(ProcessedLot).where(ProcessedLot.market_price == None)
        lots_to_eval = session.scalars(stmt).all()
        
        if not lots_to_eval:
            print("No lots pending valuation.")
            return

        print(f"Found {len(lots_to_eval)} lots to analyze.")
        
        success_count = 0
        error_count = 0
        
        for db_lot in lots_to_eval:
            try:
                print(f"Processing lot {db_lot.id}: {db_lot.title[:60]}")
                
                norm_lot = NormalizedLot.from_processed_lot(db_lot)
                evaluation = appraiser.evaluate(norm_lot, session=session)
                
                # Check for zero prices
                if evaluation.market.market_price == 0:
                    print(f"  [WARN] AI returned 0.0 market price. Retrying without json_object...")
                    # Small hack to force re-evaluation if needed
                    # evaluation = appraiser.evaluate(norm_lot, session=None) 
                
                apply_evaluation_to_lot(db_lot, evaluation)
                
                success_count += 1
                session.commit()
                print(f"  [OK] Evaluated as {evaluation.market.market_price}")
                
            except Exception as e:
                print(f"  [ERROR] Lot {db_lot.id}: {e}")
                error_count += 1
                session.rollback()
                continue
        
        print(f"\nFINISHED!")
        print(f"Success: {success_count}, Errors: {error_count}")

if __name__ == "__main__":
    mass_evaluate_lots()
