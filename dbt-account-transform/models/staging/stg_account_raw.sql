{{
    config(
        materialized='view'
    )
}}

/*
    Reads raw account data from GCS CSV files loaded into account_raw_data table.
*/

select
    account_id,
    customer_id,
    account_type,
    sort_code,
    account_number,
    opened_date_raw
from {{ source('gcs_raw', 'account_raw_data') }}
