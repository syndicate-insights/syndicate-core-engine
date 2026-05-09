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
    Enriched address model.
    Combines line1, city, postcode, and country into a full_address field.
    Loads data from the staged address table.
*/

select
    address_id,
    customer_id,
    line1,
    city,
    postcode,
    country,
    concat(line1, ', ', city, ', ', postcode, ', ', country) as full_address,
    current_timestamp() as processed_at
from {{ ref('stg_address_raw') }}
