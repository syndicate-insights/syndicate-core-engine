{{
    config(
        materialized='incremental',
        incremental_strategy='insert_overwrite',
        partition_by={
            'field': 'processed_at',
            'data_type': 'timestamp',
            'granularity': 'day'
        },
        on_schema_change='append_new_columns',
        post_hook=[
            "insert into {{ var('bq_dataset') }}.processed_files_metadata (file_name, processed_at) select distinct source_file, current_timestamp() from {{ this }} where source_file not in (select file_name from {{ var('bq_dataset') }}.processed_files_metadata where file_name is not null)"
        ]
    )
}}

/*
    Enriched address model.
    Combines line1, city, postcode, and country into a full_address field.
    Appends new rows from unprocessed CSV files only.
    After each run, a post-hook records the processed file names in the metadata table.
*/

select
    address_id,
    customer_id,
    line1,
    city,
    postcode,
    country,
    source_file,
    concat(line1, ', ', city, ', ', postcode, ', ', country) as full_address,
    current_timestamp() as processed_at
from {{ ref('stg_address_raw') }}

{% if is_incremental() %}
    where source_file not in (
        select pfm.file_name
        from {{ ref('processed_files_metadata') }} as pfm
        where pfm.file_name is not null
    )
{% endif %}
