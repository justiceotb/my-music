import discogs_client
import openpyxl
import argparse

from version import __version__
# import json

# DISCOGS_TOKEN = ''
COLUMN_HEADERS=['Album ID', 'Album', 'Year', 'Artists', 'Track Number', 'Track', 'Track Artists', 'Styles', 'Format', 'Notes']


def main():
    parser = argparse.ArgumentParser(description='Create an Excel spreadsheet or tracks from a Discogs collection')
    parser.add_argument('--token', 
                        dest='token',
                        required=True,
                        help='Your Discgos API token')
    parser.add_argument('--file', 
                        dest='filename',
                        default='tracks.xlsx',
                        help='The spreadhseet name, including .xlsx. Default is tracks.xlsx')

    args = parser.parse_args()

    release_list = []

    # Create the spreadsheet object
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = 'All Songs'
    sheet.append(COLUMN_HEADERS)

    # print(args.token)

    # Connect to Discogs
    d = discogs_client.Client('AllSongs/1.0', user_token=args.token)
    
    try:
        me = d.identity()
    except Exception as ex:
        print('Oh no! This error occurred: {}'.format(ex))
        print('Closing...')
        return

    loop = 0
    count = 0

    print("Ok {}, let's get these tracks...".format(me.name))

    for release in me.collection_folders[0].releases:
        loop += 1
        count += 1
        if loop >= 20:
            loop = 0
            # print('Still going... [{} tracks so far]'.format(count))
            # wb.save(args.filename)
            # print('Done! [{}]'.format(count))
            # return
        try:
            note = release.notes[2]['value']
        except Exception as ex:
            note = ''

        if release.release.id in release_list:
            print('Duplicate release {} {}, skipping'.format(release.release.id, release.release.title))
        else:
            release_list.append(release.release.id)

            discogs_format = ''
            for format in release.release.formats:
                try:
                    format_name = format['name']
                except Exception as e:
                    format_name = ''

                try:
                    format_desc = ' '.join(format['descriptions'])
                except Exception as e:
                    format_desc = ''

                discogs_format = '{} {} {}'.format(discogs_format, format_name, format_desc).strip()

            for track in release.release.tracklist:
                track_artists = ''
                for artists in track.artists:
                    track_artists = '{} {}'.format(track_artists, artists.name).strip()

                id = release.release.id
                al_title = release.release.title
                # print(al_title)
                year = release.release.year
                try:
                    sort = release.release.artists_sort
                except Exception:
                    sort = ''
                
                pos = track.position
                tr_title = track.title
                tr_artists = track_artists
                # try:
                #     notes = release.release.notes
                # except Exception:
                #     notes = ''

                song = [id, 
                        al_title,
                        year,
                        sort,
                        pos,
                        tr_title,
                        tr_artists,
                        ' '.join(release.release.styles),
                        discogs_format,
                        note]

                sheet.append(song)

    wb.save(args.filename)
    print('Done! [{}]'.format(count))


if __name__ == "__main__":
    main()
