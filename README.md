# AMB Dynamic Tariff for Home Assistant

Unofficial Home Assistant custom integration for the public **AMB (Azienda Multiservizi Bellinzona) Dynamic Tariff** schedule.

The integration reads the public AMB tariff chart endpoint and exposes the current tariff, next tariff change, today's and tomorrow's schedules, and a low-tariff binary sensor.

## Installation with HACS

1. Open HACS in Home Assistant.
2. Add this repository as a **Custom repository**.
3. Select category **Integration**.
4. Install **AMB Dynamic Tariff**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration** and search for **AMB Dynamic Tariff**.

No `configuration.yaml` changes are required.

## Entities

- Current tariff (`low`, `high`, or `unknown`)
- Next tariff
- Next change
- Today's schedule
- Tomorrow's schedule
- Low tariff binary sensor
- Last update diagnostic sensor

The schedule sensors expose the complete periods in their `periods` attribute.

## Data source

Data is retrieved from the public AMB tariff chart endpoint used by the official AMB website. The endpoint currently supplies 15-minute tariff points. AMB chart timestamps are interpreted as Swiss/local tariff wall-clock values to match the official chart.

## Notes

- Refresh interval: 15 minutes.
- Duplicate timestamps are de-duplicated.
- Tariff prices are intentionally not included; this integration only handles the dynamic high/low schedule.
- This project is unofficial and is not affiliated with or endorsed by AMB.

## Changelog

### 0.1.3
- Restored Home Assistant's canonical `strings.json` translation source.
- Fixed entity-name localization while keeping English (GB), English and Italian translations.

### 0.1.2
- Improved Home Assistant localization.
- Added English (GB), English and Italian translations.
- Added localized display states.

### 0.1.1
- Fixed the two-hour shift observed during Swiss summer time.

### 0.1.0
- Initial version.
