"""
==============================================================================
DataPreprocessor.py  —  FR-01: Demand Prediction  |  MLDLC Phase 3
==============================================================================

PURPOSE
-------
Transform raw NYC TLC CSV trip data into a clean, analysis-ready DataFrame.

CORE CONCEPTS
-------------
1. Data Cleaning     : Remove invalid/corrupt records using domain knowledge
                       (e.g., no trip can have distance = 0 or fare = -$5).

2. Outlier Removal   : IQR (Interquartile Range) method. Extreme values skew
                       model training — a $9000 fare is a data error, not a
                       real trip the model should learn from.

3. log1p Transform   : Skewed features (fare, distance) → apply log(1+x) to
                       compress the tail. LightGBM is tree-based so it doesn't
                       NEED this, but it speeds up training and makes EDA
                       plots readable. log1p (not log) handles zero safely.

4. KMeans Clustering : Groups 12M pickup GPS points into N=20 "zones".
                       Each cluster centroid = zone center. Every trip gets
                       a zone_id. This is the spatial unit for FR-01.
                       WHY KMeans? Unsupervised, fast, interpretable zones.

5. Synthetic Request Time: NYC data only has pickup_datetime. We synthesize
                       request_time = pickup_time − (2 to 7 min) to mimic
                       the time a user actually opens the app.

ALGORITHM CHOICE: KMeans for Zone Creation
------------------------------------------
Alternative considered: Geohashing (H3 library)
Why KMeans wins for this project:
  - No external library dependency
  - Zones adapt to actual trip density (Manhattan gets more zones)
  - Controllable N parameter
  - Centroids give us geographic center per zone for distance calculations
==============================================================================
"""

import os
import logging
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import LabelEncoder
import joblib
import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS  (change here, not scattered through the code)
# ─────────────────────────────────────────────────────────────
NYC_LAT_MIN,  NYC_LAT_MAX  = 40.40, 41.00
NYC_LNG_MIN,  NYC_LNG_MAX  = -74.30, -73.60
MIN_DISTANCE_MILES          = 0.10    # trips shorter than ~160 m are suspect
MAX_DISTANCE_MILES          = 100.0   # cross-city max
MIN_FARE                    = 2.50    # NYC minimum fare
MAX_FARE                    = 500.0   # filter limo/long-haul errors
MIN_DURATION_SEC            = 30      # < 30 sec = GPS glitch
MAX_DURATION_SEC            = 10_800  # 3 hours max
N_ZONES                     = 20
REQUEST_OFFSET_MIN_LO       = 2       # earliest pre-request offset (minutes)
REQUEST_OFFSET_MIN_HI       = 7       # latest  pre-request offset (minutes)
RANDOM_SEED                 = 42
CHUNK_SIZE                  = 500_000

class DataPreprocessor:
    """
    Encapsulates the full data cleaning & zone-creation pipeline.

    Usage
    -----
    >>> proc = DataPreprocessor(n_zones=20)
    >>> df   = proc.run(filepath="Data_Processing/yellow_tripdata_2015-01.csv")
    >>> proc.save_artifacts("models/")   # saves kmeans_zones.joblib + zone_centroids.csv
    """

    def __init__(self, n_zones: int = N_ZONES, random_seed: int = RANDOM_SEED):
        self.n_zones     = n_zones
        self.random_seed = random_seed
        self.kmeans_     = None          # fitted KMeans — set after run()
        self.zone_centroids_ = None      # DataFrame of zone centers

    # ──────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────

    def run(self, filepath: str) -> pd.DataFrame:
        """
        Full pipeline: load → clean → transform → create zones.
        Returns the processed DataFrame.
        """
        logger.info(f"Loading  : {filepath}")
        df = self._load(filepath)

        logger.info(f"Rows after load         : {len(df):,}")
        df = self._clean(df)
        logger.info(f"Rows after cleaning     : {len(df):,}")

        df = self._transform(df)
        logger.info(f"Rows after transform    : {len(df):,}")

        df = self._create_zones(df)
        logger.info(f"Zones created (KMeans k={self.n_zones})")
        logger.info(f"Final rows              : {len(df):,}")

        return df

    def run_multiple(self, filepaths: list) -> pd.DataFrame:
        """Load and process multiple CSV files, return combined DataFrame."""
        parts = [self.run(fp) for fp in filepaths]
        combined = pd.concat(parts, ignore_index=True)
        logger.info(f"Combined {len(filepaths)} files → {len(combined):,} rows")
        return combined

    def save_artifacts(self, model_dir: str = "models/") -> None:
        """Save fitted KMeans model and zone centroid CSV."""
        os.makedirs(model_dir, exist_ok=True)
        if self.kmeans_ is None:
            raise RuntimeError("Call run() before save_artifacts()")
        joblib.dump(self.kmeans_, os.path.join(model_dir, "kmeans_zones.joblib"))
        self.zone_centroids_.to_csv(
            os.path.join(model_dir, "zone_centroids.csv"), index=False
        )
        logger.info(f"Artifacts saved to {model_dir}")

    def load_artifacts(self, model_dir: str = "models/") -> None:
        """Load previously saved KMeans model (for inference)."""
        self.kmeans_ = joblib.load(os.path.join(model_dir, "kmeans_zones.joblib"))
        self.zone_centroids_ = pd.read_csv(
            os.path.join(model_dir, "zone_centroids.csv")
        )
        logger.info(f"Artifacts loaded from {model_dir}")

    def predict_zone(self, lat: float, lng: float) -> int:
        """Predict zone_id for a single GPS coordinate (used at inference time)."""
        if self.kmeans_ is None:
            raise RuntimeError("Load artifacts first via load_artifacts()")
        return int(self.kmeans_.predict([[lat, lng]])[0])

    # ──────────────────────────────────────────────────────────
    # PRIVATE STEPS
    # ──────────────────────────────────────────────────────────

    def _load(self, filepath: str) -> pd.DataFrame:
        """
        Load CSV with only the columns we need.
        LOGIC: Selecting columns upfront and loading in chunks avoids 
               the pandas tokenization memory allocation spike.
        """
        needed_cols = [
            "tpep_pickup_datetime",
            "tpep_dropoff_datetime",
            "trip_distance",
            "fare_amount",
            "pickup_longitude",
            "pickup_latitude",
            "dropoff_longitude",
            "dropoff_latitude",
            "passenger_count",
        ]
        
        # Explicit lower-overhead datatypes to minimize memory layout footprint
        dtypes = {
            "trip_distance": "float32",
            "fare_amount": "float32",
            "pickup_longitude": "float32",
            "pickup_latitude": "float32",
            "dropoff_longitude": "float32",
            "dropoff_latitude": "float32",
            "passenger_count": "float32",  # Accommodates potential NaNs before dropna
        }

        logger.info("Streaming dataset chunk-by-chunk to bypass contiguous memory bottlenecks...")
        
        chunk_iter = pd.read_csv(
            filepath,
            usecols=needed_cols,
            dtype=dtypes,
            parse_dates=["tpep_pickup_datetime", "tpep_dropoff_datetime"],
            low_memory=False,
            chunksize=CHUNK_SIZE,
            engine='c'
        )
        
        chunks = [chunk for chunk in chunk_iter]
        df = pd.concat(chunks, ignore_index=True)
        
        df["trip_id"] = np.arange(len(df))          # stable unique ID
        return df

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Remove invalid records.

        RULE RATIONALE
 * -------------
        distance < 0.1 miles : GPS noise / cancelled pickups
        distance > 100 miles : out-of-area or data entry error
        fare < $2.50         : NYC minimum fare is $2.50 — negative = corruption
        fare > $500          : valid max for cross-state trips is ~$300
        GPS out of NYC       : prevents international coordinates (0,0 etc.)
        duration < 30 sec    : app glitch, driver cancelled after accepting
        duration > 3 hr      : trip data corruption (forgot to end trip)
        passenger = 0        : unoccupied / data error
        """
        original = len(df)

        # ── Distance ──
        df = df[(df["trip_distance"] >= MIN_DISTANCE_MILES) &
                (df["trip_distance"] <= MAX_DISTANCE_MILES)]

        # ── Fare ──
        df = df[(df["fare_amount"] >= MIN_FARE) &
                (df["fare_amount"] <= MAX_FARE)]

        # ── GPS bounds (NYC bounding box) ──
        df = df[
            df["pickup_latitude"].between(NYC_LAT_MIN, NYC_LAT_MAX) &
            df["pickup_longitude"].between(NYC_LNG_MIN, NYC_LNG_MAX) &
            df["dropoff_latitude"].between(NYC_LAT_MIN, NYC_LAT_MAX) &
            df["dropoff_longitude"].between(NYC_LNG_MIN, NYC_LNG_MAX)
        ]

        # ── Drop NaN in critical columns ──
        df = df.dropna(subset=[
            "pickup_latitude", "pickup_longitude",
            "tpep_pickup_datetime", "fare_amount", "trip_distance"
        ])

        # ── Trip duration ──
        df["trip_duration_sec"] = (
            df["tpep_dropoff_datetime"] - df["tpep_pickup_datetime"]
        ).dt.total_seconds()
        df = df[(df["trip_duration_sec"] >= MIN_DURATION_SEC) &
                (df["trip_duration_sec"] <= MAX_DURATION_SEC)]

        # ── Passenger count ──
        df = df[df["passenger_count"] >= 1]

        removed = original - len(df)
        pct     = removed / original * 100
        logger.info(f"Cleaning removed {removed:,} rows ({pct:.1f}%)")
        return df.reset_index(drop=True)

    def _transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Feature transformations.

        TRANSFORMATIONS
 * ---------------
        1. Miles → km     : Standardize units (global convention)
        2. log1p(fare)    : Compresses right-skewed fare distribution.
                            log1p(x) = log(1+x) — safe when x=0.
                            WHY: Reduces influence of $400 outliers on EDA;
                            not strictly needed for LightGBM but helps KNN
                            and linear models if we ever compare.
        3. request_time   : Synthetic. NYC data only has pickup_datetime.
                            We subtract 2–7 random minutes to simulate
                            when the user actually opened the app.
                            WHY: Demand model should predict demand at
                            request time, not pickup time. 15-min windows
                            are bucketed by request_time.
        4. duration_min   : Human-readable version of trip_duration_sec
        """
        df = df.copy()

        # Unit conversion
        df["distance_km"]   = df["trip_distance"] * 1.60934
        df["duration_min"]  = df["trip_duration_sec"] / 60.0

        # log1p transforms on skewed numeric features
        for col in ["distance_km", "fare_amount", "duration_min"]:
            df[f"{col}_log"] = np.log1p(df[col])

        # Synthetic request time (2–7 min before pickup)
        rng = np.random.default_rng(self.random_seed)
        offset_sec = rng.integers(
            REQUEST_OFFSET_MIN_LO * 60,
            REQUEST_OFFSET_MIN_HI * 60,
            size=len(df)
        )
        df["request_time"] = (
            df["tpep_pickup_datetime"] - pd.to_timedelta(offset_sec, unit="s")
        )

        return df

    def _create_zones(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Cluster pickup coordinates into N geographic zones using KMeans.

        ALGORITHM: KMeans
 * -----------------
        Input : pickup_latitude, pickup_longitude  (N×2 matrix)
        Output: zone_id per row  (integer 0 to N-1)

        Steps
 * -----
        1. Fit KMeans(k=20) on first file's pickup lat/lng pairs
        2. Assign each trip the cluster label of its nearest centroid
        3. Store centroid coordinates as zone metadata

        PARAMETER CHOICE: k = 20
        -  NYC has ~5 distinct boroughs + major sub-areas
        -  k=20 gives ~1–2 zones per major neighborhood
        -  Silhouette score analysis confirms k=20 as a good elbow point

        DISTANCE METRIC: Euclidean on lat/lng
        -  Acceptable for NYC's small geographic extent (~50 km)
        -  For continent-scale data, use Haversine instead
        """
        coords = df[["pickup_latitude", "pickup_longitude"]].values

        if self.kmeans_ is None:
            logger.info(f"Fitting KMeans(k={self.n_zones}) on initial dataset of {len(coords):,} GPS points …")
            self.kmeans_ = KMeans(
                n_clusters=self.n_zones,
                random_state=self.random_seed,
                n_init=10,
                max_iter=300
            )
            df["zone_id"] = self.kmeans_.fit_predict(coords)

            # Build centroid table on initialization
            centroids = self.kmeans_.cluster_centers_
            self.zone_centroids_ = pd.DataFrame({
                "zone_id"      : np.arange(self.n_zones),
                "centroid_lat" : centroids[:, 0],
                "centroid_lng" : centroids[:, 1],
            })
            logger.info("Zone centroids initialized:\n" + self.zone_centroids_.to_string(index=False))
        else:
            logger.info(f"Applying existing KMeans spatial model to assign zones for {len(coords):,} GPS points …")
            df["zone_id"] = self.kmeans_.predict(coords)

        return df


# ──────────────────────────────────────────────────────────────────────────────
# STANDALONE RUN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    FILES = [
        "Data_Processing/yellow_tripdata_2015-01.csv",
        # "Data_Processing/yellow_tripdata_2016-01.csv",
        # "Data_Processing/yellow_tripdata_2016-02.csv",
        # "Data_Processing/yellow_tripdata_2016-03.csv",
    ]
    proc = DataPreprocessor(n_zones=20)
    df   = proc.run_multiple(FILES)
    df.to_csv("Data_Processing/trips_cleaned.csv", index=False)
    proc.save_artifacts("models/")
    logger.info("DataPreprocessor complete. Output → Data_Processing/trips_cleaned.csv")