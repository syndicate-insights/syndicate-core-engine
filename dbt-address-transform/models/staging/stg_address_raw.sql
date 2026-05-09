{{
    config(
        materialized='view'
    )
}}

/*
    Reads raw address data from GCS CSV files loaded into address_raw_data table.
*/

select
    address_id,
    customer_id,
    line1,
    city,
    postcode,
    country
from {{ source('gcs_raw', 'address_raw_data') }}
