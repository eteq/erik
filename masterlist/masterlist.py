#!/usr/bin/env python
"""
Script/functions for generating the SAGA master host list.

(Initially written by Erik Tollerud, but you should always trust git commit logs
over whatever you find in a comment at the top of the file!)



Directions for use
------------------

Assuming you have git installed, here's the way to get the code:

git clone https://github.com/saga-survey/saga-code.git

That will create a directory "saga-code" wherever you run in. Then cd into saga-
code/masterlist and you should see a file called "masterlist.py" in that
directory.  That file has all the code for generating the master list.

In addition to that, you'll need the various catalogs. (We don't include them in
the saga repository because they're quite large, and github frowns on storing
large datasets.)  The various catalogs are listed above - for the EDD files,
you'll want *all* the columns for each catalog, downloaded as comma-separated
value files.  For the NSA, the fits file is what you need.  Just download it all
into the directory this script is in, or symlink from this script's directory to
wherever you have the data.


Once you've got all the data collected, you can do ``python masterlist.py`` and
it should generate the catalog for you as a file called "masterlist.dat".  If
you want to fiddle with the velocity cutoff  in the ``if __name__ ==
'__main__'`` block - it should be obvious where it is there.

If you want to use that catalog in a python session/script, you should be able
to just do:

import masterlist cat = masterlist.load_master_catalog()

As long as you're in the masterlist directory.  Alternatively, you can just
copy-and-paste the stuff in the ``if __name__ == '__main__'`` block if you want
even more control.

Note that building the catalog can be rather memory-intensive - it works fine
for me with 16GB of memory, but much less than that might get pretty slow...


Data files
----------

These are required:
* LEDA.csv (from the EDD: http://edd.ifa.hawaii.edu/ - the "LEDA" table with all columns, comma-separated)
* 2MRS.csv (from the EDD: http://edd.ifa.hawaii.edu/ - the "2MRS K<11.75 " table with all columns, comma-separated)
* EDD.csv (from the EDD: http://edd.ifa.hawaii.edu/ - the "EDD Distances" table with all columns, comma-separated)
* KKnearbygal.csv (from the EDD: http://edd.ifa.hawaii.edu/ - the "Updated Nearby Galaxy Catalog" table with all columns, comma-separated)
* nsa_v0_1_2.fits (from http://www.nsatlas.org/)

Optional, with info below:
* full-sky 2MASS XSC catalog from IRSA (assumed filename '2mass_xsc_irsa.tab' - see below for how to get it)



2MASS XSC
---------

If you want to use the full 2MASS catalog to get K-band mags for more objects,
you'll need to get it from IRSA with the following procedure:

1. Go to http://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-scan?mission=irsa&submit=Select&projshort=2MASS
   and choose "2MASS All-Sky Extended Source Catalog (XSC)", then hit "Select".
2. Hit the "All Sky Search" radio button
3. Check the columns you want.  You'll certaintly need "ra"/"dec", as well as
   "k_m_ext" and "cc_flg".  Everything else you mat not need, but might be
   helpful later.
4. Hit "Run query".  IRSA will build a catalog, and you can use the "process
   monitor" to watch until it's done.
5. Once it's finished, hit the "done" link.  In the page that takes you to,
   there's a "View Table" link near the bottom of the page.  Download the file
   that link points to, and rename/symlink it to the name '2mass_xsc_irsa.tab'
   in your working directory.

6dF Catalog
-----------

Go to http://www-wfau.roe.ac.uk/6dFGS/SQL.html and execute this query:

SELECT specid,targetname,obsra,obsdec,z_helio, zfinalerr, quality
FROM spectra
WHERE (quality=3 or quality=4) and progID <=8

Then save it to "6dF.csv"



"""
from __future__ import division, print_function

import os

import numpy as np

from astropy import units as u
from astropy.cosmology import WMAP9  # for Z->distances


def load_edd_csv(fn):
    from astropy.io import ascii

    return ascii.read(fn, delimiter=',', Reader=ascii.Basic, guess=False)


def load_2mass_xsc(fn):
    from astropy.io import ascii

    #first need to figure out where the column info header is and where the data begins
    with open(fn) as f:
        colinfo_idx = dat_start_idx = None
        for i, l in enumerate(f):
            if colinfo_idx is None:
                if l.startswith('|'):
                    colinfo_idx = i
            elif not l.startswith('|'):
                #first data line after the | lines
                dat_start_idx = i
                break
        else:
            raise ValueError('load_2mass_xsc tried to load something that does '
                             'not look like an IRSA 2MASS output')

    return ascii.read(fn, format='fixed_width', guess=False,
                      header_start=colinfo_idx, data_start=dat_start_idx,
                      fill_values=[('', '0'), ('null', '0')])


def load_nsa(fn='nsa_v0_1_2.fits', verbose=False):
    """
    This loads the NSA *and* breaks the FNugriz fields into distinct columns

    Not that it drops columns that have more complex dimensionality, like the
    radial profiles or stokes parameters
    """
    from astropy.io import fits
    from astropy import table

    tab = table.Table(fits.getdata(fn))

    newcols = []
    for nm in tab.colnames:
        col = tab[nm]
        if len(col.shape) == 2:
            if col.shape[1] == 7:
                for i, band in enumerate('FNugriz'):
                    newcols.append(table.Column(name=nm + '_' + band, data=col[:, i]))
            elif col.shape[1] == 5:
                for i, band in enumerate('ugriz'):
                    newcols.append(table.Column(name=nm + '_' + band, data=col[:, i]))
            else:
                if verbose:
                    print('Column', nm, "has weird dimension", col.shape, 'skipping')
        elif len(col.shape) > 2:
            if verbose:
                print('Column', nm, "has weird dimension", col.shape, 'skipping')
        else:
            newcols.append(col)

    newtab = table.Table()
    newtab.add_columns(newcols)
    return newtab


def generate_catalog(leda, twomass, edd, kknearby, nsa, matchtolerance=1*u.arcmin):
    from astropy import table
    from astropy.coordinates import ICRS

    #first join the small ones, because they both have "dist" columns
    small = table.join(edd, kknearby, keys=['pgc'], table_names=['edd', 'kk'], join_type='outer')

    #now join them with LEDA
    #we call the second one "kk" because the only thing shared with LEDA is 'm21' on the KK catalog
    ledaj = table.join(leda, small, keys=['pgc'], table_names=['leda', 'kk'], join_type='outer')

    #add in the 2mass stuff
    #call the first one "eddkk" because the shared columns are all either in the EDD or KK
    ledaj2 = table.join(ledaj, twomass, keys=['pgc'], table_names=['eddkk', '2mass'], join_type='outer')

    #now cross-match with NSA - need to match on RA/Dec because no PGC #s in NSA
    ral, decl = ledaj2['al2000'], ledaj2['de2000']
    lmsk = (~ral.mask) & (~decl.mask)
    lcoo = ICRS(u.hour * ral[lmsk], u.degree * decl[lmsk])
    nsacoo = ICRS(u.degree * nsa['RA'], u.degree * nsa['DEC'])

    idx, dd, dd3d = nsacoo.match_to_catalog_sky(lcoo)
    matchpgc = -np.ones(len(idx), dtype=int)  # non-matches get -1
    dmsk = dd < matchtolerance  # only match those with a closest neighbor w/i tol
    matchpgc[dmsk] = ledaj2['pgc'][lmsk][idx[dmsk]]

    if 'pgc' in nsa.colnames:
        nsa['pgc'] = matchpgc
    else:
        nsa.add_column(table.Column(name='pgc', data=matchpgc))

    return table.join(ledaj2, nsa, keys=['pgc'], table_names=['leda', 'nsa'], join_type='outer')


def manually_tweak_simplified_catalog(simplifiedcat):
    """
    This just updates a few entries in the catalog that seem to be missing
    velocities for unclear reasons
    """
    from astropy.coordinates import ICRS
    from astropy.constants import c

    infolines = """
    46.480833  54.266111   2051.8    No velocity in NED PGC 011632
    46.704583  54.588333   2859.3    No velocity in NED
    50.288333  66.921667   2637.1    Velocity in NED, but no LEDA entry
    99.794583 -1.5075000   2887.9    No velocity in NED
    102.57375 -2.8605556   2699.9    No velocity in NED
    102.93875 -3.6077778   2867.4    No velocity in NED
    114.40708 -26.746667   2964.5    Velocity in NED, LEDA source, but not HyperLeda
    116.32750 -32.516667   2162.0    Velocity in NED, LEDA source, but not HyperLeda
    123.74833 -30.866667   1761.0    Velocity in NED, PGC 023091 (note in NED re; position error?)
    """.split('\n')[1:-1]

    ras = []
    decs = []
    vels = []
    for l in infolines:
        ls = l.split()
        ras.append(float(ls[0]))
        decs.append(float(ls[1]))
        vels.append(float(ls[2]))

    updatecoos = ICRS(ras*u.deg, decs*u.deg)
    catcoos = ICRS(simplifiedcat['RA'].view(np.ndarray)*u.deg,
                   simplifiedcat['Dec'].view(np.ndarray)*u.deg)
    idx, dd, d3d = updatecoos.match_to_catalog_sky(catcoos)

    simplifiedcat = simplifiedcat.copy()
    simplifiedcat['vhelio'][idx] = vels = np.array(vels)
    simplifiedcat['distance'][idx] = WMAP9.luminosity_distance(
        vels / c.to(u.km / u.s).value).to(u.Mpc).value

    return simplifiedcat


def filter_catalog(mastercat, vcut, musthavenirphot=False):
    """
    Removes entries in the simplified catalog to meet the  master catalog selection
    criteria.

    Parameters
    ----------
    mastercat : Table
        A table like that output from `generate_catalog`
    vcut : `astropy.units.Quantity` with velocity units
        The cutoff velocity to filter
    musthavenirphot : bool
        If True, filters out everything that does *not* have K, i, or z-band
        magnitudes
    """

    #possible point of confusion:`msk` is True where we want to *keep* so
    #something, because it is used at the end as a bool index into the catalog.
    #The MaskedColumn `mask` attribute is the opposite - True is *masked*

    #filter anything without an RA or Dec
    msk = ~(mastercat['RA'].mask | mastercat['Dec'].mask)

    #also remove everything without a distance - this includes all w/velocities,
    #because for those the distance comes from assuming hubble flow
    msk = msk & (~mastercat['distance'].mask)


    # remove everything that has a velocity > `vcut`
    if vcut is not None:
        msk = msk & (mastercat['vhelio'] < vcut.to(u.km/u.s).value)

    if musthavenirphot:
        msk = msk & ((~mastercat['i'].mask) | (~mastercat['z'].mask) | (~mastercat['I'].mask) | (~mastercat['K'].mask))

    return mastercat[msk]


def simplify_catalog(mastercat, quickld=True):
    """
    Removes most of the unnecessary columns from the master catalog and joins
    fields where relevant

    Parameters
    ----------
    mastercat : astropy.table.Table
        The table from generate_catalog
    quickld : bool
        If True, means do the "quick" version of the luminosity distance
        calculation (takes <1 sec as opposed to a min or so, but is only good
        to a few kpc)
    """
    from astropy import table

    from astropy.constants import c

    ckps = c.to(u.km/u.s).value

    tab = table.Table()

    #RADEC:
    ras = mastercat['al2000']*15
    ras[~mastercat['RA'].mask] = mastercat['RA'][~mastercat['RA'].mask]
    decs = mastercat['de2000']
    decs[~mastercat['DEC'].mask] = mastercat['DEC'][~mastercat['DEC'].mask]

    tab.add_column(table.MaskedColumn(name='RA', data=ras, units=u.deg))
    tab.add_column(table.MaskedColumn(name='Dec', data=decs, units=u.deg))

    #Names/IDs:
    pgc = mastercat['pgc'].copy()
    pgc.mask = mastercat['pgc'] < 0
    tab.add_column(table.MaskedColumn(name='PGC#', data=pgc))
    tab.add_column(table.MaskedColumn(name='NSAID', data=mastercat['NSAID']))
    #do these in order of how 'preferred' the object name is.
    nameorder = ('Objname', 'Name_eddkk', 'objname', 'Name_2mass')  # this is: EDD, KK, LEDA, 2MASS
    #need to figure out which has the *largest* name strings, because we have a fixed number of characters
    largestdt = np.dtype('S1')
    for nm in nameorder:
        if mastercat.dtype[nm] > largestdt:
            largestdt = mastercat.dtype[nm]
            largestdtnm = nm
    names = mastercat[largestdtnm].copy()  # these will all be overwritten - just use it for shape
    for nm in nameorder:
        msk = ~mastercat[nm].mask
        names[msk] = mastercat[nm][msk]
    tab.add_column(table.MaskedColumn(name='othername', data=names))

    #After this, everything should have either an NSAID, a PGC#, or a name (or more than one)

    #VELOCITIES/redshifts
    #start with LEDA
    vs = mastercat['v'].astype(float)
    v_errs = mastercat['e_v'].astype(float)

    #Now add vhelio from the the EDD
    eddvhel = mastercat['Vhel_eddkk']
    vs[~eddvhel.mask] = eddvhel[~eddvhel.mask]
    #EDD has no v-errors, so mask them
    v_errs[~eddvhel.mask] = 0
    v_errs.mask[~eddvhel.mask] = True

    #then the NSA, if available
    vs[~mastercat['ZDIST'].mask] = mastercat['ZDIST'][~mastercat['ZDIST'].mask] * ckps
    v_errs[~mastercat['ZDIST_ERR'].mask] = mastercat['ZDIST_ERR'][~mastercat['ZDIST_ERR'].mask] * ckps

    #finally, KK when present
    kkvh = mastercat['Vh']
    vs[~kkvh.mask] = kkvh[~kkvh.mask]
    #KK has no v-errors, so mask them
    v_errs[~kkvh.mask] = 0
    v_errs.mask[~kkvh.mask] = True

    #DISTANCES
    dist = mastercat['Dist_edd'].copy()
    dist[~mastercat['Dist_kk'].mask] = mastercat['Dist_kk'][~mastercat['Dist_kk'].mask]

    #for those *without* EDD or KK, use the redshift's luminosity distance
    premsk = dist.mask.copy()
    zs = vs[premsk]/ckps
    if quickld:
        ldx = np.linspace(zs.min(), zs.max(), 1000)
        ldy = WMAP9.luminosity_distance(ldx).to(u.Mpc).value
        ld = np.interp(zs, ldx, ldy)
    else:
        ld = WMAP9.luminosity_distance(zs).to(u.Mpc).value

    dist[premsk] = ld
    dist.mask[premsk] = vs.mask[premsk]

    distmod = 5 * np.log10(dist) + 25  # used in phot section

    tab.add_column(table.MaskedColumn(name='vhelio', data=vs))
    tab.add_column(table.MaskedColumn(name='vhelio_err', data=v_errs))
    tab.add_column(table.MaskedColumn(name='distance', data=dist, units=u.Mpc))

    #NIR PHOTOMETRY
    tab.add_column(table.MaskedColumn(name='i', data=mastercat['ABSMAG_i'] + distmod))
    tab.add_column(table.MaskedColumn(name='z', data=mastercat['ABSMAG_z'] + distmod))
    tab.add_column(table.MaskedColumn(name='I', data=mastercat['it']))
    tab.add_column(table.MaskedColumn(name='K', data=mastercat['K_tc']))

    return tab


def add_twomassxsc(mastercat, twomassxsc, tol=3*u.arcmin, copymastercat=False):
    """
    This matches `mastercat` to the 2MASS XSC and sets K-band mags in the master
    catalog for anything that doesn't have a K mag and has an XSC entry within
    `tol`.  Returns the new catalog (which is a copy if `copymastercat` is True,
    otherwise it is just the same table as `mastercat`)

    With a fiducial cut of cz < 4000 km/s, this goes from 30% with K-band mags
    to 65%
    """
    from astropy.coordinates import ICRS

    ctwomass = ICRS(u.deg*twomassxsc['ra'].view(np.ndarray), u.deg*twomassxsc['dec'].view(np.ndarray))
    cmaster = ICRS(u.deg*mastercat['RA'].view(np.ndarray), u.deg*mastercat['Dec'].view(np.ndarray))
    idx, dd, d3d = cmaster.match_to_catalog_sky(ctwomass)

    matches = dd < tol

    if copymastercat:
        mastercat = mastercat.copy()
    mK = mastercat['K']

    premask = mK.mask.copy() # those that *do* have a K-band mag from the 2MRS/EDD

    #Sets those that are not in 2MRS/EDD to have the K-band total mag from the XSC
    mK[premask] = twomassxsc['k_m_ext'][idx][premask]
    #now masks those of the above that are not within tol
    mK.mask[~matches] = True
    mK.mask[~premask] = False  # fix it up so that those with 2MRS/EDD values are not masked

    return mastercat


def load_master_catalog(fn='mastercat.dat'):
    from astropy.io import ascii
    return ascii.read(fn, delimiter=',')


def x_match_tests(cattomatch, tol=1*u.arcmin, vcuts=None):
    """
    Does a bunch of cross-matches with other catalogs. `cattomatch` must be an
    `ICRS` object or a table.

    This depends on having a bunch of other catalogs in the current directory
    that aren't in the git repo, so you may want to just ask Erik if you want
    to run this.

    `vcut`s are appliced to RC3 and 6dF

    data files:
    RC3 comes from the vizier version.
    hosts.dat are the original SAGA hosts file before the masterlist work
    atlas3d_* are from Marla from somewhere...
    zcat.fits is from vizier's ZCAT copy
    """
    import RC3
    from astropy.io import ascii, fits
    from astropy.coordinates import ICRS

    from astropy.constants import c

    if cattomatch.__class__.__name__.lower() == 'table':
        ra, dec = cattomatch['RA'], cattomatch['Dec']
        cattomatch = ICRS(u.Unit(ra.unit)*ra.view(np.ndarray), u.Unit(dec.unit)*dec.view(np.ndarray))

    rc3, rc3_coo = RC3.load_rc3()
    rc3wv = rc3[~rc3['cz'].mask]
    rc3wv_coo = rc3_coo[~rc3['cz'].mask]
    if vcuts:
        msk = rc3wv['cz'] < vcuts.to(u.km/u.s).value
        rc3wv = rc3wv[msk]
        rc3wv_coo = rc3wv_coo[msk]

    a3de = ascii.read('atlas3d_e.dat', data_start=3, format='fixed_width')
    a3dsp = ascii.read('atlas3d_sp.dat', data_start=3, format='fixed_width')
    a3de_coo = ICRS(u.deg*a3de['RA'], u.deg*a3de['DEC'])
    a3dsp_coo = ICRS(u.deg*a3dsp['RA'], u.deg*a3dsp['DEC'])

    nsah = ascii.read('hosts.dat')
    nsah_coo = ICRS(u.hourangle*nsah['RA'], u.deg*nsah['DEC'])

    sixdf = ascii.read('6dF.csv',guess=False,delimiter=',')
    sixdf_coo = ICRS(u.deg*sixdf['obsra'], u.deg*sixdf['obsdec'])
    if vcuts:
        msk = sixdf['z_helio'] < (vcuts/c).decompose().value
        sixdf = sixdf[msk]
        sixdf_coo = sixdf_coo[msk]

    zcat = fits.getdata('zcat.fits')
    zcat_coo = ICRS(u.deg*zcat['_RAJ2000'], u.deg*zcat['_DEJ2000'])
    if vcuts:
        msk = (zcat['Vh'] < (vcuts).to(u.km/u.s).value)
        msk = msk & (zcat['Vh'] > -1000)  #get rid of those w/o velocities
        zcat = zcat[msk]
        zcat_coo = zcat_coo[msk]


    rc3_idx, rc3_d = rc3_coo.match_to_catalog_sky(cattomatch)[:2]
    rc3wv_idx, rc3wv_d = rc3wv_coo.match_to_catalog_sky(cattomatch)[:2]
    a3de_idx, a3de_d = a3de_coo.match_to_catalog_sky(cattomatch)[:2]
    a3dsp_idx, a3dsp_d = a3dsp_coo.match_to_catalog_sky(cattomatch)[:2]
    nsah_idx, nsah_d = nsah_coo.match_to_catalog_sky(cattomatch)[:2]
    sixdf_idx, sixdf_d = sixdf_coo.match_to_catalog_sky(cattomatch)[:2]
    zcat_idx, zcat_d = zcat_coo.match_to_catalog_sky(cattomatch)[:2]

    rc3_nomatch = rc3_d > tol
    rc3wv_nomatch = rc3wv_d > tol
    a3de_nomatch = a3de_d > tol
    a3dsp_nomatch = a3dsp_d > tol
    nsah_nomatch = nsah_d > tol
    sixdf_nomatch = sixdf_d > tol
    zcat_nomatch = zcat_d > tol

    print('RC3 non-matches: {0} of {1}'.format(np.sum(rc3_nomatch), len(rc3_nomatch)))
    print('RC3 with v non-matches: {0} of {1}'.format(np.sum(rc3wv_nomatch), len(rc3wv_nomatch)))
    print('ATLAS3D E non-matches: {0} of {1}'.format(np.sum(a3de_nomatch), len(a3de_nomatch)))
    print('ATLAS3D Spiral non-matches: {0} of {1}'.format(np.sum(a3dsp_nomatch), len(a3dsp_nomatch)))
    print('NSA Hosts non-matches: {0} of {1}'.format(np.sum(nsah_nomatch), len(nsah_nomatch)))
    print('6dF non-matches: {0} of {1}'.format(np.sum(sixdf_nomatch), len(sixdf_nomatch)))
    print('ZCAT non-matches: {0} of {1}'.format(np.sum(zcat_nomatch), len(zcat_nomatch)))

    dct = {}
    for nm in ('rc3', 'rc3wv', 'a3de', 'a3dsp', 'nsah', 'sixdf', 'zcat'):
        dct[nm+'_match'] = (locals()[nm])[~locals()[nm + '_nomatch']]
        dct[nm+'_nomatch'] = (locals()[nm])[locals()[nm + '_nomatch']]
        dct[nm+'_catidx'] = (locals()[nm+'_idx'])
    return dct


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('-q', '--quiet', action='store_true')
    p.add_argument('outfn', nargs='?', help="If this is not given, the catalog won't be saved", default=None)
    args = p.parse_args()

    if args.quiet:
        #silences the print function
        print = lambda s: None

    print("Loading LEDA catalog...")
    leda = load_edd_csv('LEDA.csv')
    print("Loading 2MRS catalog...")
    twomass = load_edd_csv('2MRS.csv')
    print("Loading EDD catalog...")
    edd = load_edd_csv('EDD.csv')
    print("Loading KK nearby catalog...")
    kknearby = load_edd_csv('KKnearbygal.csv')
    nsa = load_nsa()

    if os.path.exists('2mass_xsc_irsa.tab'):
        print("Loading 2MASS XSC...")
        twomassxsc = load_2mass_xsc('2mass_xsc_irsa.tab')
    else:
        twomassxsc = None

    #these variables are just for convinience in interactive work
    cats = [leda, twomass, edd, kknearby, nsa]
    eddcats = [leda, twomass, edd, kknearby]

    print('Generating master catalog...')
    mastercat0 = generate_catalog(*cats)
    print('Simplifying master catalog...')
    mastercat1 = simplify_catalog(mastercat0)
    #mastercat1 = manually_tweak_simplified_catalog(mastercat1)
    print('Filtering master catalog...')
    mastercat = filter_catalog(mastercat1, vcut=4000*u.km/u.s,)

    if twomassxsc:
        print('Supplementing with 2MASS XSC K mags')
        mastercat = add_twomassxsc(mastercat, twomassxsc)
    else:
        print("You don't have a copy of the 2MASS XSC, so the fraction with K-band mags will be lower")

    if args.outfn is not None:
        print('Writing master catalog to {args.outfn}...'.format(**locals()))

        oldmpo = str(np.ma.masked_print_option)
        try:
            np.ma.masked_print_option.set_display('')
            mastercat.write(args.outfn, format='ascii', delimiter=',')
        finally:
            np.ma.masked_print_option.set_display(oldmpo)
