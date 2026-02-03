# DATA DICTIONARY For Intelligent Ride Demand–Supply Optimization System

## 1️⃣ TRIPS TABLE

### **Purpose**

Stores historical ride transaction data used for:

- Demand forecasting
    
- Drop-pattern detection
    
- Fare analysis
    

### **Trips**

| Column Name       | Data Type          | Description                  |
| ----------------- | ------------------ | ---------------------------- |
| trip_id           | UUID / BIGINT (PK) | Unique trip identifier       |
| user_id           | UUID / BIGINT      | Passenger identifier         |
| cab_id            | UUID / BIGINT      | Assigned cab                 |
| pickup_zone       | VARCHAR            | Zone where trip started      |
| drop_zone         | VARCHAR            | Zone where trip ended        |
| request_time      | TIMESTAMP          | Time when ride was requested |
| pickup_time       | TIMESTAMP          | Actual pickup time           |
| drop_time         | TIMESTAMP          | Trip completion time         |
| trip_distance_km  | FLOAT              | Distance travelled (km)      |
| trip_duration_min | FLOAT              | Trip duration (minutes)      |
| fare_amount       | FLOAT              | Final fare charged           |
| payment_type      | VARCHAR            | Cash / UPI / Card            |
| trip_status       | VARCHAR            | Completed / Cancelled        |

---

## 2️⃣ CABS TABLE

### **Purpose**

Maintains real-time and historical cab state for:

- Supply monitoring
    
- Idle time analysis
    
- Recommendation targeting
    

### **cabs**

|Column Name|Data Type|Description|
|---|---|---|
|cab_id|UUID / BIGINT (PK)|Unique cab identifier|
|driver_id|UUID / BIGINT|Driver identifier|
|current_zone|VARCHAR|Current cab location|
|latitude|FLOAT|Current latitude|
|longitude|FLOAT|Current longitude|
|status|VARCHAR|idle / busy / offline|
|last_trip_id|UUID / BIGINT|Last completed trip|
|last_updated|TIMESTAMP|Last status update|
|idle_start_time|TIMESTAMP|When cab became idle|

---

## 3️⃣ USER BEHAVIOR TABLE

### **Purpose**

Captures passenger behavior patterns used for:

- Drop clustering
    
- Return-trip prediction
    
- Demand anticipation
    

### **user_behavior**

``

|Column Name|Data Type|Description|
|---|---|---|
|behavior_id|UUID / BIGINT (PK)|Record identifier|
|user_id|UUID / BIGINT|Passenger identifier|
|drop_zone|VARCHAR|Location where user got dropped|
|drop_time|TIMESTAMP|Time of drop|
|frequent_zone_flag|BOOLEAN|Is frequent drop location|
|return_trip_requested|BOOLEAN|Whether user pre-booked return|
|return_trip_time|TIMESTAMP|Planned return time|
|trip_purpose|VARCHAR|Office / Event / Personal (optional)|

---

## 4️⃣ FEEDBACK TABLE

### **Purpose**

Stores driver responses to system suggestions for:

- Acceptance learning
    
- Adaptive recommendation volume
    
- Feedback loop intelligence
    

### **feedback**

``

|Column Name|Data Type|Description|
|---|---|---|
|feedback_id|UUID / BIGINT (PK)|Feedback record|
|cab_id|UUID / BIGINT|Cab receiving suggestion|
|recommended_zone|VARCHAR|Suggested relocation zone|
|suggestion_time|TIMESTAMP|Time suggestion sent|
|accepted|BOOLEAN|Accepted / Rejected|
|response_time_sec|INTEGER|Time taken to respond|
|rejection_reason|VARCHAR|Distance / Traffic / Fuel|
|effective|BOOLEAN|Led to successful trip|

---

## 5️⃣ EXTERNAL FACTORS TABLE

### **Purpose**

Stores contextual data affecting demand & supply:

- Weather
    
- Traffic
    
- Events (explicit + inferred)
    

### **external_factors**

|Column Name|Data Type|Description|
|---|---|---|
|factor_id|UUID / BIGINT (PK)|Factor record|
|zone|VARCHAR|Affected location|
|time_slot|TIMESTAMP|Time window|
|weather_type|VARCHAR|Clear / Rain / Storm|
|weather_score|FLOAT|Demand impact score|
|traffic_index|FLOAT|Congestion level (0–1)|
|known_event_flag|BOOLEAN|Pre-known event|
|inferred_event_flag|BOOLEAN|ML-detected event|
|event_intensity|FLOAT|Event impact strength|

---

# 📌 RELATIONSHIP SUMMARY (Important for Report)

- `trips.cab_id` → `cabs.cab_id`
    
- `trips.user_id` → `user_behavior.user_id`
    
- `feedback.cab_id` → `cabs.cab_id`
    
- `user_behavior.drop_zone` → `external_factors.zone`