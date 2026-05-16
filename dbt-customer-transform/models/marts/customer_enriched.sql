{{
    config(
        materialized='incremental',
        incremental_strategy='insert_overwrite',
        partition_by={
            'field': 'processed_at',
            'data_type': 'timestamp',
            'granularity': 'day'
        },
        on_schema_change='append_new_columns'
    )
}}

/*
    Enriched customer model.
    Cleans the phone number by stripping all non-digit characters to produce
    a normalised phone_number field suitable for BigQuery.
*/

select
    customer_id,
    first_name,
    last_name,
    dob_raw,
    email,
    phone,
    regexp_replace(phone, r'[^0-9]', '') as phone_number,
    current_timestamp() as processed_at
from {{ ref('stg_customer_raw') }}
