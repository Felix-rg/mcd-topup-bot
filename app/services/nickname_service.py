# File: app/services/nickname_service.py
import httpx
import logging

async def check_game_nickname(game_code: str, user_id: str, zone_id: str = "") -> str:
    """
    Fungsi inti untuk mengecek Nickname berdasarkan ID.
    Saat ini menggunakan mode simulasi.
    """
    if not user_id:
        return None

    try:
        # TODO: Di sinilah nanti kita letakkan integrasi ke API Pihak ke-3 (misal: API Games)
        # Contoh jika pakai API asli:
        # async with httpx.AsyncClient() as client:
        #     response = await client.post("URL_API_CHECKER", json={"game": game_code, "id": user_id, "zone": zone_id})
        #     data = response.json()
        #     if data['status'] == 'success':
        #         return data['nickname']
        
        # --- MODE SIMULASI UNTUK TESTING ---
        # Mengembalikan nama palsu agar alur checkout bisa dites
        simulated_nickname = f"Player_{user_id[:4]}***"
        
        # Simulasi jika ID kependekan (salah ketik)
        if len(user_id) < 5:
            return None 

        return simulated_nickname

    except Exception as e:
        logging.error(f"Error checking nickname: {str(e)}")
        return None
