import os, tempfile, unittest
from pathlib import Path

class DatabaseSmokeTest(unittest.TestCase):
    def test_legacy_databases_and_character_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['DATA_DIR']=tmp
            os.environ['STATE_DB']=str(Path(tmp)/'state.db')
            os.environ['BALANCE_DB']=str(Path(tmp)/'balance_system.db')
            os.environ['COLLECTION_DB']=str(Path(tmp)/'collection.db')
            import waifux.database as d
            d.add_character({'id':'test-1','name':'Sample','anime':'Test','rarity':'Starter','image':'file-id','price':10})
            with d.db() as con:
                d.adjust_balance(123,cash=50,con=con)
                d.add_to_collection(123,'test-1','tester',con)
                self.assertEqual(d.get_balance(123,con)[0],50)
                self.assertEqual(len(d.collection_rows(123,con)),1)
            self.assertEqual(d.get_character('test-1')['name'],'Sample')

    def test_handlers_import_and_application_registers(self):
        from telegram.ext import Application
        from waifux.handlers import register_all
        app=Application.builder().token('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi').build()
        register_all(app)
        handlers=[h for group in app.handlers.values() for h in group]
        self.assertGreaterEqual(len(handlers),35)
        self.assertTrue(any(h.__class__.__name__=='InlineQueryHandler' for h in handlers))

if __name__=='__main__': unittest.main()
