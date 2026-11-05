import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

class DatabaseSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        root=Path(cls.tmp.name)
        cls.env_keys=('DATA_DIR','STATE_DB','BALANCE_DB','COLLECTION_DB')
        cls.old_env={key:os.environ.get(key) for key in cls.env_keys}
        os.environ['DATA_DIR']=str(root)
        os.environ['STATE_DB']=str(root/'state.db')
        os.environ['BALANCE_DB']=str(root/'balance_system.db')
        os.environ['COLLECTION_DB']=str(root/'collection.db')
        # Seed legacy files before the bot database module opens them.
        with sqlite3.connect(os.environ['BALANCE_DB']) as con:
            con.execute('CREATE TABLE user_balance(user_id INTEGER PRIMARY KEY,cash INTEGER DEFAULT 0,diamonds INTEGER DEFAULT 0)')
            con.execute('INSERT INTO user_balance VALUES(77,1234,44)')
        with sqlite3.connect(os.environ['COLLECTION_DB']) as con:
            con.execute('CREATE TABLE collections(user_id INTEGER,username TEXT,waifu_id TEXT,name TEXT,anime TEXT,rarity TEXT,image TEXT)')
            con.execute('INSERT INTO collections VALUES(77,"legacy-user","old-1","Legacy Hero","Legacy Anime","Starter","old-file-id")')
        import waifux.database as db
        cls.db=db

    @classmethod
    def tearDownClass(cls):
        for key,value in cls.old_env.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        cls.tmp.cleanup()

    def test_legacy_balances_and_collections_survive_schema_setup(self):
        d=self.db
        self.assertEqual(d.get_balance(77),(1234,44))
        old=d.get_character('old-1')
        self.assertIsNotNone(old)
        self.assertEqual(old['name'],'Legacy Hero')
        self.assertEqual(len(d.collection_rows(77)),1)
        with d.db() as con:
            cols={row['name'] for row in con.execute('PRAGMA collection.table_info(collections)')}
        self.assertIn('favorite',cols)

    def test_new_character_wallet_and_collection_operations(self):
        d=self.db
        d.add_character({'id':'test-1','name':'Sample','anime':'Test','rarity':'Starter','image':'file-id','price':10})
        with d.db() as con:
            d.adjust_balance(123,cash=50,con=con)
            d.add_to_collection(123,'test-1','tester',con)
            self.assertEqual(d.get_balance(123,con)[0],50)
            self.assertEqual(len(d.collection_rows(123,con)),1)
        self.assertEqual(d.get_character('test-1')['name'],'Sample')

    def test_handlers_import_and_application_registers_expected_commands(self):
        from telegram.ext import Application, CommandHandler, InlineQueryHandler
        from waifux.handlers import register_all
        app=Application.builder().token('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi').build()
        register_all(app)
        handlers=[h for group in app.handlers.values() for h in group]
        commands={c for h in handlers if isinstance(h,CommandHandler) for c in h.commands}
        self.assertIn('mines',commands)
        self.assertIn('marketplace',commands)
        self.assertIn('addsudo',commands)
        self.assertEqual(sum('pay' in h.commands for h in handlers if isinstance(h,CommandHandler)),1)
        self.assertTrue(any(isinstance(h,InlineQueryHandler) for h in handlers))

if __name__=='__main__':
    unittest.main()
