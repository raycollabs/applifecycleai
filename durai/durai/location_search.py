"""Sample: Search 1000 meters around a point. Return authorized locations in distance order."""
import math

from .db import transaction


def nearby(latitude, longitude, radius_meters, *, limit=20, database='durai_test', role='durai_runtime'):
    # Sample: Find points within 1000 meters. Return at most 20 points.
    for value, lower, upper in ((latitude,-90,90),(longitude,-180,180),(radius_meters,0,20000000)):
        if type(value) not in (int,float) or not lower <= value <= upper or not math.isfinite(value):
            raise ValueError('Coordinate or radius outside its range')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Location limit must be 1 to 100')
    with transaction(role,database) as db:
        return db.execute("""
            WITH distances AS (
                SELECT location_id, latitude, longitude,
                    2 * 6371008.8 * asin(sqrt(LEAST(1, GREATEST(0,
                        power(sin(radians(latitude-%s)/2),2)
                        + cos(radians(latitude))*cos(radians(%s))*power(sin(radians(longitude-%s)/2),2)
                    )))) AS distance_meters
                FROM durai_transactions.location WHERE application_id=durai_core.application_id()
            )
            SELECT * FROM distances WHERE distance_meters<=%s ORDER BY distance_meters,location_id LIMIT %s
        """,(latitude,latitude,longitude,radius_meters,limit)).fetchall()
