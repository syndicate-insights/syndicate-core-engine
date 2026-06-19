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
    Enriched account model.
    Sets account_type to INVESTMENT where:
      - The sort code's last digit is a prime number (2, 3, 5, 7)
      - The account number's last two digits are both even (0, 2, 4, 6, 8)
*/

select
    account_id,
    customer_id,
    sort_code,
    account_number,
    opened_date_raw,
    case
        when cast(substr(regexp_replace(sort_code, r'[^0-9]', ''), -1, 1) as int64) in (2, 3, 5, 7)
         and mod(cast(substr(account_number, -2, 1) as int64), 2) = 0
         and mod(cast(substr(account_number, -1, 1) as int64), 2) = 0
        then 'INVESTMENT'
        else account_type
    end as account_type,
    current_timestamp() as processed_at
from {{ ref('stg_account_raw') }}
