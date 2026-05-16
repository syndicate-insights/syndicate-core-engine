{{
    config(
        materialized='view'
    )
}}

/*
    Reads raw customer data from GCS CSV files loaded into customer_raw_data table.
*/

select
    customer_id,
    first_name,
    last_name,
    dob_raw,
    email,
    phone
from {{ source('gcs_raw', 'customer_raw_data') }}
