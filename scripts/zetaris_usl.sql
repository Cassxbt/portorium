-- Portorium data layer: the Unified Semantic Layer `portorium` and its data-quality rules.
-- Statements are separated by lines containing only ";;" (COMPILE USL keeps its inner semicolons).
-- CSV inputs are built by scripts/build_zetaris_inputs.py and copied to customer-data/shared/portorium/.

CREATE LIGHTNING DATABASE portorium_files DESCRIBE BY "Portorium snapshot files - HTS 2026 Rev 21 and CBP CROSS rulings"
;;
CREATE LIGHTNING FILESTORE TABLE hts_line FROM portorium_files FORMAT CSV OPTIONS (path "file:///home/zetaris/customer-data/shared/portorium/hts_line.csv", header "true", inferSchema "false")
;;
CREATE LIGHTNING FILESTORE TABLE ruling FROM portorium_files FORMAT CSV OPTIONS (path "file:///home/zetaris/customer-data/shared/portorium/ruling.csv", header "true", inferSchema "false")
;;
CREATE LIGHTNING FILESTORE TABLE ruling_code FROM portorium_files FORMAT CSV OPTIONS (path "file:///home/zetaris/customer-data/shared/portorium/ruling_code.csv", header "true", inferSchema "false")
;;
CREATE LIGHTNING DATABASE portorium_live DESCRIBE BY "Live sources - USITC current HTS release"
;;
CREATE LIGHTNING REST TABLE hts_release FROM portorium_live REQUEST (endpoint "https://hts.usitc.gov/reststop/currentRelease", method "GET", http_encoding "URLENCODED", response_type "JSON", insecure_ssl "false") HEADER () BODY ()
;;
CREATE NAMESPACE IF NOT EXISTS lightning.metastore
;;
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.portorium
;;
COMPILE USL IF NOT EXISTS portorium DEPLOY NAMESPACE lightning.metastore.portorium DDL
CREATE TABLE hts_line (
  hts10 varchar(10) NOT NULL PRIMARY KEY,
  htsno varchar(16),
  description varchar(2000),
  general_rate varchar(300),
  general_rate_inherited varchar(300)
);
CREATE TABLE hts_code (
  code varchar(10) NOT NULL PRIMARY KEY,
  code_len int
);
CREATE TABLE hts_release (
  name varchar(40) NOT NULL PRIMARY KEY,
  description varchar(200)
);
CREATE TABLE ruling (
  ruling_number varchar(20) NOT NULL PRIMARY KEY,
  ruling_date date,
  collection varchar(10),
  subject varchar(500),
  revoked boolean,
  modified boolean
);
CREATE TABLE ruling_code (
  ruling_code_id varchar(30) NOT NULL PRIMARY KEY,
  ruling_number varchar(20) NOT NULL FOREIGN KEY REFERENCES ruling(ruling_number),
  hts10 varchar(10) NOT NULL FOREIGN KEY REFERENCES hts_line(hts10),
  code_position int
)
;;
UPDATE USL lightning.metastore.portorium.portorium SET DESCRIPTION 'Portorium - HTS 2026 Rev 21 schedule, CBP CROSS rulings and the codes they cite. A precedent is living only if its code passes the data-quality rules here.'
;;
ACTIVATE USL TABLE lightning.metastore.portorium.portorium.hts_line AS
SELECT hts10, htsno, description, general_rate, general_rate_inherited
FROM (
  SELECT COALESCE(code_digits, '') AS hts10, htsno, description, general_rate,
         last(NULLIF(general_rate, ''), true) OVER (ORDER BY CAST(row_idx AS INT)
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS general_rate_inherited,
         code_len
  FROM portorium_files.hts_line
) t
WHERE code_len = '10'
;;
ACTIVATE USL TABLE lightning.metastore.portorium.portorium.hts_code AS
SELECT COALESCE(code_digits, '') AS code, CAST(code_len AS INT) AS code_len
FROM portorium_files.hts_line
WHERE code_len IN ('8', '10')
;;
ACTIVATE USL TABLE lightning.metastore.portorium.portorium.hts_release AS
SELECT COALESCE(name, '') AS name, description FROM portorium_live.hts_release
;;
ACTIVATE USL TABLE lightning.metastore.portorium.portorium.ruling AS
SELECT COALESCE(ruling_number, '') AS ruling_number, CAST(ruling_date AS DATE) AS ruling_date, collection, subject,
       CAST(revoked AS BOOLEAN) AS revoked, CAST(modified AS BOOLEAN) AS modified
FROM portorium_files.ruling
;;
ACTIVATE USL TABLE lightning.metastore.portorium.portorium.ruling_code AS
SELECT COALESCE(ruling_code_id, '') AS ruling_code_id, COALESCE(ruling_number, '') AS ruling_number,
       COALESCE(hts10, '') AS hts10, CAST(code_position AS INT) AS code_position
FROM portorium_files.ruling_code
;;
REGISTER DQ code_in_schedule TABLE lightning.metastore.portorium.portorium.ruling_code AS hts10 IN (SELECT hts10 FROM lightning.metastore.portorium.portorium.hts_line)
;;
REGISTER DQ parent_survives TABLE lightning.metastore.portorium.portorium.ruling_code AS substr(hts10, 1, 8) IN (SELECT code FROM lightning.metastore.portorium.portorium.hts_code)
;;
REGISTER DQ code_is_numeric TABLE lightning.metastore.portorium.portorium.ruling_code AS hts10 RLIKE '^[0-9]{10}$'
;;
REGISTER DQ not_future_dated TABLE lightning.metastore.portorium.portorium.ruling AS ruling_date <= current_date()
;;
REGISTER DQ subject_present TABLE lightning.metastore.portorium.portorium.ruling AS length(trim(subject)) > 0
;;
REGISTER DQ rate_resolved TABLE lightning.metastore.portorium.portorium.hts_line AS general_rate_inherited IS NOT NULL
;;
REGISTER DQ release_is_snapshot TABLE lightning.metastore.portorium.portorium.hts_release AS name = '2026HTSRev21'
;;
LIST DQ USL lightning.metastore.portorium.portorium
