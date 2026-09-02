"""==============================================================================
main.py  —  FR-01 MLDLC Pipeline Orchestrator
==============================================================================
Run from the PROJECT ROOT (23 Ride hailing/) — NOT from ML_Development/:
    cd "E:\Personal Things\02 Projects\23 Ride hailing"
    python ML_Development/main.py --mode all

Or run individual phases:
    python ML_Development/main.py --mode preprocess
    python ML_Development/main.py --mode features
    python ML_Development/main.py --mode train

ROOT ISSUE (why it was silent before)
--------------------------------------
The old version used relative paths like "Data_Processing/trips_cleaned.csv".
When you ran it from ML_Development/, Python looked for:
    ML_Development/Data_Processing/trips_cleaned.csv  ← DOES NOT EXIST

Fix: compute PROJECT_ROOT from this file's location. Always use absolute paths.
=============================================================================="""

import argparse
import logging
import os
import sys

# ── Compute absolute project root ──────────────────────────────────────────
# This file is at: <project_root>/ML_Development/main.py
# So PROJECT_ROOT = one folder up from this file's directory
THIS_FILE    = os.path.abspath(__file__)            # .../ML_Development/main.py
ML_DEV_DIR   = os.path.dirname(THIS_FILE)           # .../ML_Development/
PROJECT_ROOT = os.path.dirname(ML_DEV_DIR)          # .../23 Ride hailing/

# Add ML_Development to sys.path so we can import DataPreprocessor etc.
if ML_DEV_DIR not in sys.path:
    sys.path.insert(0, ML_DEV_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# ── Absolute file paths (safe from any working directory) ──────────────────
def P(relative_path: str) -> str:
    """Convert project-relative path to absolute path."""
    return os.path.join(PROJECT_ROOT, relative_path)

RAW_FILES = [
    P("Data_Processing/yellow_tripdata_2015-01.csv"),
    # P("Data_Processing/yellow_tripdata_2016-01.csv"),
    # P("Data_Processing/yellow_tripdata_2016-02.csv"),
    # P("Data_Processing/yellow_tripdata_2016-03.csv"),
]

CLEANED_CSV  = P("Data_Processing/trips_cleaned.csv")
FEATURES_CSV = P("Data_Processing/features.csv")
WEATHER_CSV  = P("Data_Processing/weather_scores.csv")   # optional
EVENT_CSV    = P("Data_Processing/event_scores.csv")      # optional
MODEL_DIR    = P("models/")

def _check_raw_files():
    """Verify at least one raw CSV exists before starting pipeline."""
    found = [f for f in RAW_FILES if os.path.exists(f)]
    missing = [f for f in RAW_FILES if not os.path.exists(f)]
    
    if missing:
        for f in missing:
            logger.warning(f"  NOT FOUND : {f}")
            
    if not found:
        logger.error(
            "\n[ERROR] No raw CSV files found."
            f"\nExpected them at: {P('Data_Processing/')}"
            "\nMake sure you are running from the project root OR that"
            "\nthe yellow_tripdata_*.csv files are in Data_Processing/"
        )
        sys.exit(1)
        
    logger.info(f"Found {len(found)}/{len(RAW_FILES)} raw CSV files.")
    return found

def phase_preprocess():
    """MLDLC Phase 3 — Data Preprocessing"""
    logger.info("=" * 60)
    logger.info("PHASE 3 : DATA PREPROCESSING")
    logger.info(f"  Project root : {PROJECT_ROOT}")
    logger.info("=" * 60)
    
    available_files = _check_raw_files()
    from DataPreprocessor import DataPreprocessor
    proc = DataPreprocessor(n_zones=20)
    df   = proc.run_multiple(available_files)
    
    os.makedirs(os.path.dirname(CLEANED_CSV), exist_ok=True)
    df.to_csv(CLEANED_CSV, index=False)
    proc.save_artifacts(MODEL_DIR)
    
    logger.info(f"[DONE] Preprocessing complete.")
    logger.info(f"  Output : {CLEANED_CSV}")
    logger.info(f"  Rows   : {len(df):,}")

def phase_features():
    """MLDLC Phase 5 — Feature Engineering"""
    logger.info("=" * 60)
    logger.info("PHASE 5 : FEATURE ENGINEERING")
    logger.info("=" * 60)
    
    if not os.path.exists(CLEANED_CSV):
        logger.error(
            f"[ERROR] trips_cleaned.csv not found at {CLEANED_CSV}"
            "\nRun Phase 3 first:  python ML_Development/main.py --mode preprocess"
        )
        sys.exit(1)
        
    from FeatureEngineer import FeatureEngineer
    fe = FeatureEngineer()
    df = fe.run(
        trips_csv   = CLEANED_CSV,
        weather_csv = WEATHER_CSV,
        event_csv   = EVENT_CSV,
    )
    df.to_csv(FEATURES_CSV, index=False)
    
    train, test = fe.time_based_split(df)
    train.to_csv(P("Data_Processing/train.csv"), index=False)
    test.to_csv(P("Data_Processing/test.csv"),   index=False)
    
    logger.info(f"[DONE] Feature engineering complete.")
    logger.info(f"  Output      : {FEATURES_CSV}")
    logger.info(f"  Total rows  : {len(df):,}")
    logger.info(f"  Train rows  : {len(train):,}")
    logger.info(f"  Test rows   : {len(test):,}")
    logger.info(f"  Features    : {len(df.columns)} columns")

def phase_train():
    """MLDLC Phases 6+7 — Model Training + Evaluation"""
    logger.info("=" * 60)
    logger.info("PHASE 6/7 : MODEL TRAINING + EVALUATION")
    logger.info("=" * 60)
    
    if not os.path.exists(FEATURES_CSV):
        logger.error(
            f"[ERROR] features.csv not found at {FEATURES_CSV}"
            "\nRun Phase 5 first:  python ML_Development/main.py --mode features"
        )
        sys.exit(1)
        
    from DemandForecaster import DemandForecaster
    fc = DemandForecaster()
    fc.run(FEATURES_CSV)
    
    logger.info(f"[DONE] Training complete.")
    logger.info(f"  Model saved : {MODEL_DIR}demand_model.joblib")

def phase_all():
    """Full pipeline end-to-end."""
    logger.info("=" * 60)
    logger.info("RUNNING FULL MLDLC PIPELINE (Phases 3 → 5 → 6/7)")
    logger.info("=" * 60)
    phase_preprocess()
    phase_features()
    phase_train()
    logger.info("\n[✓] Full FR-01 MLDLC pipeline complete.\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FR-01 Demand Forecasting MLDLC Pipeline",
        epilog=(
            "Run from project root:\n"
            "  python ML_Development/main.py --mode all\n"
            "  python ML_Development/main.py --mode preprocess\n"
            "  python ML_Development/main.py --mode features\n"
            "  python ML_Development/main.py --mode train\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--mode",
        choices=["all", "preprocess", "features", "train"],
        default="all",
        help="Pipeline phase to run (default: all)",
    )
    args = parser.parse_args()
    
    logger.info(f"Mode: --mode {args.mode}")
    logger.info(f"Project root detected: {PROJECT_ROOT}")
    
    if   args.mode == "all":        phase_all()
    elif args.mode == "preprocess": phase_preprocess()
    elif args.mode == "features":   phase_features()
    elif args.mode == "train":      phase_train()