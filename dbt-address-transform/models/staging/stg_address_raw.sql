{{
    config(
        materialized='view'
    )
}}

/*
    Reads raw address data from the GCS external table.
    Filters out any files that have already been processed
    (tracked in processed_files_metadata).
*/

select
    address_id,
    customer_id,
    line1,
    city,
    postcode,
    country,
    _FILE_NAME as source_file
from {{ source('gcs_raw', 'address_raw_external') }}
where _FILE_NAME not in (
    select pfm.file_name
    from {{ ref('processed_files_metadata') }} as pfm
    where pfm.file_name is not null
)
