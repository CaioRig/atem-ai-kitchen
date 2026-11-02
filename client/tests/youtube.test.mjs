import test from 'node:test';
import assert from 'node:assert/strict';
import { youtubeVideoId } from '../public/js/stage.js';

test('links de vídeo: apenas HTTPS e hosts oficiais, sem credenciais', () => {
  for (const url of ['https://www.youtube.com/watch?v=abcdefghijk&feature=share', 'https://youtu.be/abcdefghijk', 'https://m.youtube.com/shorts/abcdefghijk']) {
    assert.equal(youtubeVideoId(url), 'abcdefghijk');
  }
  for (const url of ['javascript:alert(1)', 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://youtube.com@evil.test/watch?v=abcdefghijk', 'http://youtu.be/abcdefghijk', 'https://www.youtube.com/results?search_query=pao', 'https://user@youtube.com/watch?v=abcdefghijk']) {
    assert.equal(youtubeVideoId(url), undefined);
  }
});
